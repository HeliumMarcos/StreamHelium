import logging
import requests
from datetime import datetime, timedelta
from sgd.cache import Pickle, Json
from sgd.utils import STOP_WORDS
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)


class GoogleDrive:
    def __init__(self, token, cache_namespace="default"):
        """cache_namespace MUST be unique per user - the on-disk cache
        (/tmp) can be reused across requests within the same warm serverless
        instance, and without namespacing, one user's cached access token or
        drive names could leak into another user's response."""
        self.token = token
        self.page_size = 1000
        self.acc_token = Pickle(f"acctoken_{cache_namespace}.pickle")
        self.drive_names = Json(f"drivenames_{cache_namespace}.json")

        creds = Credentials.from_authorized_user_info(self.token)
        self.drive_instance = build("drive", "v3", credentials=creds)

    @staticmethod
    def qgen(string, chain="and", splitter=" ", method=None):
        out = ""
        # FIX: Forçamos buscar sempre no nome para evitar vazamento de pastas de outras temporadas
        get_method = lambda _: method if method else "name"

        cleaned_string = string.replace(".", " ").replace("'", " ").replace(":", " ").replace("-", " ")
        cleaned_string = " ".join(cleaned_string.split())

        raw_words = [w for w in cleaned_string.split(splitter) if w]
        # For very short titles, a single-letter word (e.g. the "D" in
        # "Dia D") is often essential, not noise - dropping it turns a
        # distinctive title into a much more common word and makes the
        # Drive query match far too many unrelated files. Only drop
        # 1-letter words when there's enough other content to stay
        # specific.
        if len(raw_words) <= 2:
            all_words = raw_words
        else:
            all_words = [w for w in raw_words if len(w) > 1 or w.isdigit()]

        strong_words = [w for w in all_words if w.lower() not in STOP_WORDS]

        if len(strong_words) <= 1:
            final_words = all_words 
        else:
            final_words = strong_words 

        if not final_words:
            final_words = all_words

        for word in final_words:
            if out:
                out += f" {chain} "
            out += f"{get_method(word)} contains '{word}'"
            
        return out

    def get_query(self, sm):
        out = []

        logger.debug("Titles received: %s", sm.titles)

        if sm.stream_type == "series":
            # Sem temporada e episodio nao ha o que procurar: um arquivo de
            # serie so e identificavel pela tag SxxEyy. Devolver vazio e
            # honesto; adivinhar traria a serie inteira.
            if not (str(sm.se).isdigit() and str(sm.ep).isdigit() and int(sm.se) > 0):
                logger.info("Series id without a season/episode; nothing to search for.")
                return []

            # Mudado o método para 'name' para garantir que procure as tags S01E01 no arquivo de vídeo
            seep_q = self.qgen(
                f"S{sm.se}E{sm.ep}, "
                f"s{sm.se} e{sm.ep}, "
                f"s{int(sm.se)} e{int(sm.ep)}, "
                f"season {int(sm.se)} episode {int(sm.ep)}, "
                f'"{int(sm.se)} x {int(sm.ep)}", '
                f'"{int(sm.se)} x {sm.ep}"',
                chain="or",
                splitter=", ",
                method="name",
            )
            for title in sm.titles:
                query_part = self.qgen(title)
                if not query_part: continue

                if len(title.split()) == 1:
                    clean_t = title.replace("'", " ")
                    out.append(f"name contains '{clean_t}' and ({seep_q})")
                else:
                    out.append(f"{query_part} and ({seep_q})")
        else:
            for title in sm.titles:
                q = self.qgen(title)
                if q:
                    out.append(q)
        
        return out

    def get_id_query(self, sm):
        imdb_id = getattr(sm, "id", None)
        if not imdb_id:
            return None

        if sm.stream_type == "series":
            try:
                se_raw = str(int(sm.se)) 
                ep_raw = str(int(sm.ep)) 
            except (TypeError, ValueError):
                se_raw, ep_raw = sm.se, sm.ep

            candidates = {
                f"{imdb_id}:{se_raw}:{ep_raw}",
                f"{imdb_id}:{sm.se}:{sm.ep}",
                f"{imdb_id} T{str(sm.se).zfill(2)}E{str(sm.ep).zfill(2)}",
                f"{imdb_id} S{str(sm.se).zfill(2)}E{str(sm.ep).zfill(2)}",
            }

            parts = [f"name contains '{c}'" for c in candidates]
            return " or ".join(parts)

        return f"name contains '{imdb_id}'"

    def file_list(self, file_fields):
        def callb(request_id, response, exception):
            if response:
                output.extend(response.get("files", []))
            if exception:
                logger.warning("Google Drive query failed: %s", exception)

        output = []
        if self.query:
            files = self.drive_instance.files()
            batch = self.drive_instance.new_batch_http_request()

            for q in self.query:
                logger.debug("Drive query: %s", q)

                batch_inst = files.list(
                    q=f"({q}) and trashed=false and mimeType contains 'video/'",
                    fields=f"files({file_fields})",
                    pageSize=self.page_size,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    corpora="allDrives",
                )
                batch.add(batch_inst, callback=callb)
            try:
                batch.execute()
            except Exception as e:
                logger.warning("Google Drive batch request failed: %s", e)

            return output
        return output

    def get_drive_names(self):
        def callb(request_id, response, exception):
            if response:
                self.drive_names.contents[response.get("id")] = response.get("name")

        batch = self.drive_instance.new_batch_http_request()
        drives = self.drive_instance.drives()
        
        drive_ids = set(item.get("driveId") for item in self.results if item.get("driveId"))
        
        if not drive_ids: return {}

        for drive_id in drive_ids:
            if not self.drive_names.contents.get(drive_id):
                self.drive_names.contents[drive_id] = None
                batch_inst = drives.get(driveId=drive_id, fields="name, id")
                batch.add(batch_inst, callback=callb)

        try:
            batch.execute()
        except Exception as e:
            logger.warning("Failed to fetch drive names: %s", e)

        self.drive_names.save()
        return self.drive_names.contents

    def _dedupe_and_sort(self, response):
        uids = set()

        def check_dupe(item):
            driveId = item.get("driveId", "MyDrive")
            md5Checksum = item.get("md5Checksum")
            uid = driveId + (md5Checksum if md5Checksum else item.get("id"))

            if uid in uids: return False
            uids.add(uid)
            return True

        return sorted(
            filter(check_dupe, response),
            key=lambda item: int(item.get("size", 0)),
            reverse=True,
        )

    def search(self, stream_meta):
        self.results = []
        self.query = self.get_query(stream_meta)

        # Adiciona a pesquisa por ID direto no lote principal de queries
        id_q = self.get_id_query(stream_meta)
        if id_q and id_q not in self.query:
            self.query.append(id_q)

        response = self.file_list("id, name, size, driveId, md5Checksum")
        self.len_response = 0

        if response:
            self.len_response = len(response)
            self.results = self._dedupe_and_sort(response)

        self.get_drive_names()
        return self.results

    def get_acc_token(self):
        if not self.acc_token.contents: self.acc_token.contents = {}
        expires = self.acc_token.contents.get("expires_in")
        is_expired = True
        
        if expires:
            try:
                if isinstance(expires, str): expires = datetime.fromisoformat(expires)
                is_expired = expires <= datetime.now()
            except (TypeError, ValueError) as e:
                logger.warning("Couldn't parse cached token expiry (%r): %s", expires, e)

        if is_expired:
            body = {
                "client_id": self.token["client_id"],
                "client_secret": self.token["client_secret"],
                "refresh_token": self.token["refresh_token"],
                "grant_type": "refresh_token",
            }
            # Google's current token endpoint. The old
            # www.googleapis.com/oauth2/v4/token alias still answers, but
            # this is the documented one and the same host the Worker and
            # the OAuth callback already use.
            api_url = "https://oauth2.googleapis.com/token"
            try:
                oauth_resp = requests.post(api_url, json=body).json()
                if "access_token" in oauth_resp:
                    oauth_resp["expires_in"] = timedelta(seconds=oauth_resp["expires_in"]) + datetime.now()
                    self.acc_token.contents = oauth_resp
                    self.acc_token.save()
                else:
                    logger.error("OAuth token refresh failed: %s", oauth_resp)
            except requests.exceptions.RequestException as e:
                logger.error("OAuth token refresh request failed: %s", e)

        return self.acc_token.contents.get("access_token")
