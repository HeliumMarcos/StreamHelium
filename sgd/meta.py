import os
import json
import logging
import lxml
import cchardet
import sgd.utils as ut
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from sgd.cache import Json

logger = logging.getLogger(__name__)

# How long a resolved title/year lookup is trusted before being refreshed.
# Without a TTL, a bad/partial result cached once (e.g. a flaky upstream
# response) would stick forever.
METADATA_CACHE_TTL = timedelta(days=7)


class MetadataNotFound(Exception):
    pass

class IMDb:
    def __init__(self):
        self.imdb_sg_url = f"v2.sg.media-imdb.com/suggests/t/{self.id}.json"
        self.cinemeta_url = f"v3-cinemeta.strem.io/meta/{self.type}/{self.id}.json"
        self.imdb_html_url = f"imdb.com/title/{self.id}/releaseinfo?ref_=tt_dt_aka"

        self.fetch_dest = "None"
        
        if self.get_meta_from_tmdb():
            self.fetch_dest = "TMDB_API"
            
        if self.get_meta_from_cinemeta():
            if self.fetch_dest == "None":
                self.fetch_dest = "CINEMETA"
        
        if not self.titles and self.get_meta_from_imdb_sg():
            if self.fetch_dest == "None":
                self.fetch_dest = "IMDB_SG_API"

        try:
            self.get_meta_from_imdb_html()
            if self.fetch_dest == "None" and self.titles:
                self.fetch_dest = "IMDB_HTML"
        except Exception as e:
            logger.warning("Failed to read IMDb HTML page for %s: %s", self.id, e)

        if not self.titles:
            self.fetch_dest = "NULL"
            raise MetadataNotFound(
                f"Couldn't find metadata for {self.type} {self.id}!"
            )
            
        cleaned_titles = []
        for t in self.titles:
            if t and isinstance(t, str) and len(t) > 1:
                clean_t = t.lower().strip()
                unaccented_t = ut.strip_accents(clean_t)

                if clean_t not in cleaned_titles:
                    cleaned_titles.append(clean_t)
                    
                if unaccented_t not in cleaned_titles and unaccented_t != clean_t:
                    cleaned_titles.append(unaccented_t)
                    
        self.titles = cleaned_titles

    def get_meta_from_tmdb(self):
        tmdb_key = os.environ.get("TMDB_API_KEY")
        if not tmdb_key:
            return False
            
        try:
            find_url = f"api.themoviedb.org/3/find/{self.id}?api_key={tmdb_key}&external_source=imdb_id"
            find_resp = ut.req_wrapper(find_url)
            if not find_resp: return False
            
            find_data = json.loads(find_resp)
            media_type = "movie" if self.type == "movie" else "tv"
            
            results = find_data.get(f"{media_type}_results", [])
            if not results: return False
            
            item = results[0]
            tmdb_id = item.get("id")
            
            original_title = item.get("original_title") or item.get("original_name")
            if original_title:
                self.titles.append(ut.sanitize(original_title))
                if not self.original_title:
                    self.original_title = ut.sanitize(original_title, lower=False)

            eng_title = item.get("title") or item.get("name")
            if eng_title: self.titles.append(ut.sanitize(eng_title))

            date_str = item.get("release_date") or item.get("first_air_date")
            if date_str and len(date_str) >= 4 and ut.is_year(date_str[:4]):
                self.year = date_str[:4]

            pt_url = f"api.themoviedb.org/3/{media_type}/{tmdb_id}?api_key={tmdb_key}&language=pt-BR"
            pt_resp = ut.req_wrapper(pt_url)
            if pt_resp:
                pt_data = json.loads(pt_resp)
                pt_title = pt_data.get("title") or pt_data.get("name")
                if pt_title:
                    self.titles.append(ut.sanitize(pt_title))
                    if not self.name:
                        self.name = ut.sanitize(pt_title, lower=False)

            return True

        except Exception as e:
            logger.warning("TMDB lookup failed for %s: %s", self.id, e)
            return False

    def get_meta_from_imdb_html(self):
        try:
            imdb_html = ut.req_wrapper(self.imdb_html_url, time_out=5)
            if not imdb_html: return False
            
            soup = BeautifulSoup(imdb_html, "lxml")
            table = soup.find("table", attrs={"class": "akas-table-test-only"})
            r_title_block = soup.find(
                "div", attrs={"class": "subpage_title_block__right-column"}
            )

            if r_title_block:
                h3_itemprop = r_title_block.find("h3", attrs={"itemprop": "name"})
                h4_itemprop = r_title_block.find("h4", attrs={"itemprop": "name"})

                title = ""
                display_title = ""
                if h4_itemprop:
                    raw_h4 = h4_itemprop.find("a").text
                    t_text = ut.sanitize(raw_h4) + " "
                    if "golden globe" not in t_text.lower():
                        title = t_text
                        display_title = ut.sanitize(raw_h4, lower=False) + " "

                if h3_itemprop:
                    raw_h3 = h3_itemprop.find("a").text
                    t_text = ut.sanitize(raw_h3)
                    if "golden globe" not in t_text.lower():
                        title += t_text
                        self.titles.append(title)
                        if not self.name:
                            self.name = (display_title + ut.sanitize(raw_h3, lower=False)).strip()

                    if not self.year:
                        try:
                            span_text = h3_itemprop.find("span").text.strip()
                            years = list(filter(ut.is_year, ut.num_extract(span_text)))
                            self.year = min(years) if years else None
                        except AttributeError:
                            pass

            if table:
                table_rows = table.find_all("tr")
                table_data = [tr.find_all("td") for tr in table_rows]

                titles = set()
                first_title = ut.safe_get(self.titles, 0)

                for td in table_data:
                    title_text = ut.safe_get(td, 1).text
                    if not title_text: continue
                    
                    title = ut.sanitize(title_text)
                    if "golden globe" in title.lower():
                        continue

                    if title and title != first_title:
                        if not (title.isdigit() and len(title) < 3):
                            titles.add(title)

                limit = 100 
                self.titles += list(titles)[:limit]

            return True
        except Exception as e:
            logger.warning("Failed to parse IMDb HTML page for %s: %s", self.id, e)
            return False

    def get_meta_from_imdb_sg(self):
        try:
            meta = ut.req_api(self.imdb_sg_url, key="d")
            if meta:
                self.set_meta(meta[0], year="y", title="l")
                return True
        except Exception as e:
            logger.warning("IMDb suggest lookup failed for %s: %s", self.id, e)
        return False

    def get_meta_from_cinemeta(self):
        try:
            meta = ut.req_api(self.cinemeta_url)
            if meta:
                self.set_meta(meta)
                return True
        except Exception as e:
            logger.warning("Cinemeta lookup failed for %s: %s", self.id, e)
        return False

    def set_meta(self, meta, year="year", title="name"):
        raw_title = meta.get(title, "")
        clean_title = ut.sanitize(raw_title)
        if "golden globe" not in clean_title.lower():
            self.titles.append(clean_title)
            if not self.name and clean_title:
                self.name = ut.sanitize(raw_title, lower=False)

        y = str(meta.get(year, "")).split("–")[0]
        if y and ut.is_year(y):
            self.year = y


class Meta(IMDb):
    def __init__(self, stream_type, stream_id):
        self.titles = []
        self.name = None
        self.original_title = None
        self.year = None
        self.ep = 0
        self.se = 0

        self.id_split = ut.split_stream_id(stream_id)
        self.type = stream_type
        self.stream_type = stream_type

        # --- CONVERSÃO TMDB → IMDB ---
        if self.id_split[0].lower() == "tmdb":
            tmdb_numeric_id = self.id_split[1] if len(self.id_split) > 1 else None
            if tmdb_numeric_id:
                imdb_id = self._resolve_tmdb_to_imdb(stream_type, tmdb_numeric_id)
                if imdb_id:
                    logger.info("TMDB->IMDB: tmdb:%s -> %s", tmdb_numeric_id, imdb_id)
                    # Substitui [tmdb, numeric_id, se, ep] por [imdb_id, se, ep]
                    self.id_split = [imdb_id] + self.id_split[2:]
                else:
                    logger.warning("Couldn't convert tmdb:%s to an IMDB id", tmdb_numeric_id)
        # ---------------------------------

        self.id = self.id_split[0]

        if stream_type == "series":
            try:
                self.ep = str(self.id_split[-1]).zfill(2)
                self.se = str(self.id_split[-2]).zfill(2)
            except IndexError:
                pass

        cached = Json(f"{self.id}.json")
        cached_at = cached.contents.get("cached_at")
        is_stale = True
        if cached_at:
            try:
                is_stale = datetime.fromisoformat(cached_at) + METADATA_CACHE_TTL <= datetime.now()
            except ValueError:
                is_stale = True

        if not cached.contents or is_stale:
            IMDb.__init__(self)
            cached.contents.update(self.__dict__)
            cached.contents["cached_at"] = datetime.now().isoformat()
            cached.save()
        else:
            cached.contents["se"] = self.se
            cached.contents["ep"] = self.ep
            self.__dict__.update(cached.contents)
            self.fetch_dest = "CACHE"

        logger.info("METADATA (%s): %s | Year: %s", self.fetch_dest, self.titles, self.year)

    @staticmethod
    def _resolve_tmdb_to_imdb(stream_type, tmdb_numeric_id):
        """Convert a numeric TMDB id to an IMDB id (e.g. tt1234567), caching the mapping."""
        cache = Json(f"tmdb_{tmdb_numeric_id}.json")
        if cache.contents.get("imdb_id"):
            return cache.contents["imdb_id"]

        tmdb_key = os.environ.get("TMDB_API_KEY")
        if not tmdb_key:
            logger.warning("TMDB_API_KEY is not set; can't convert TMDB id %s", tmdb_numeric_id)
            return None

        try:
            media_type = "movie" if stream_type == "movie" else "tv"
            url = f"api.themoviedb.org/3/{media_type}/{tmdb_numeric_id}/external_ids?api_key={tmdb_key}"
            resp = ut.req_wrapper(url)
            if not resp:
                return None

            data = json.loads(resp)
            imdb_id = data.get("imdb_id")

            if imdb_id:
                cache.contents["imdb_id"] = imdb_id
                cache.save()

            return imdb_id

        except Exception as e:
            logger.warning("Failed to convert TMDB id %s to IMDB: %s", tmdb_numeric_id, e)
            return None