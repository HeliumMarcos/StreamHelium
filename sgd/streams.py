import os
import logging
import urllib
import re
from sgd.ptn import parse_title
from sgd.utils import hr_size, strip_accents, STOP_WORDS

logger = logging.getLogger(__name__)


class Streams:
    def __init__(self, gdrive, stream_meta):
        self.results = []
        self.gdrive = gdrive
        self.strm_meta = stream_meta
        self.get_url = self.get_proxy_url
        self.proxy_url = os.environ.get("CF_PROXY_URL")

        if not self.proxy_url:
            self.get_url = self.get_gapi_url
            self.acc_token = gdrive.get_acc_token()

        for item in getattr(gdrive, 'results', []):
            try:
                self.item = item
                if not isinstance(self.item, dict):
                    continue
                    
                self.parsed = parse_title(str(self.item.get("name", "")))
                
                # Hardening: Previne quebra se o parse_title falhar
                if self.parsed is None:
                    class DummyParsed: pass
                    self.parsed = DummyParsed()
                
                if not hasattr(self.parsed, 'sortkeys') or not isinstance(getattr(self.parsed, 'sortkeys', None), dict):
                    self.parsed.sortkeys = {}

                self.construct_stream()
                
                # --- FILTRO INTELIGENTE ---
                if self.is_semi_valid_title(self.constructed):
                    strm_type = getattr(self.strm_meta, 'type', '')
                    if strm_type == "movie":
                        if self.is_valid_year(self.constructed):
                            self.results.append(self.constructed)
                    elif strm_type == "series":
                        # VERIFICAÇÃO CRUCIAL: Bloqueia vazamentos de outras temporadas/episódios
                        if self.is_valid_episode(self.constructed):
                            self.results.append(self.constructed)
                    else:
                        self.results.append(self.constructed)
                    
            except Exception as e:
                logger.warning("Failed to process drive item %r: %s", item.get("name"), e)
                continue

        # Ordenação inteligente
        self.results.sort(key=self.best_res, reverse=True)

    def is_valid_year(self, movie):
        sortkeys = movie.get("sortkeys", {})
        if not isinstance(sortkeys, dict): 
            sortkeys = {}
            
        file_year_str = str(sortkeys.get("year", "0"))
        meta_year_str = str(getattr(self.strm_meta, 'year', '0'))

        if file_year_str == "0" or not file_year_str.isdigit():
            return True

        try:
            file_year = int(file_year_str)
            meta_year = int(meta_year_str)
            return abs(file_year - meta_year) <= 1
        except (TypeError, ValueError):
            return True

    def is_valid_episode(self, item):
        sortkeys = item.get("sortkeys", {})
        if not isinstance(sortkeys, dict): 
            sortkeys = {}
            
        file_se = sortkeys.get("se")
        file_ep = sortkeys.get("ep")
        
        if file_se is None or file_ep is None:
            file_name = str(self.item.get("name", "")).lower()
            match = re.search(r's(\d+)\s*e(\d+)', file_name)
            if match:
                file_se, file_ep = match.groups()
            else:
                return False
                
        try:
            return int(file_se) == int(getattr(self.strm_meta, 'se', -1)) and int(file_ep) == int(getattr(self.strm_meta, 'ep', -1))
        except (ValueError, TypeError, AttributeError):
            return False

    def is_semi_valid_title(self, item):
        file_name_raw = str(self.item.get("name", ""))

        imdb_id = getattr(self.strm_meta, "id", None)
        if imdb_id and str(imdb_id).lower() in file_name_raw.lower():
            return True

        def clean_str(s):
            s = strip_accents(s)
            s = re.sub(r"[^a-zA-Z0-9]", " ", s).lower()
            return " ".join(s.split())

        # A lone letter left over after an apostrophe becomes a space is
        # ambiguous: it can be a distinctive word on its own ("Dia D", the
        # "D" in D-Day) or a contraction/possessive remnant ("Margo's" ->
        # "margo s") that a filename may instead fuse/drop entirely
        # ("Margos"). By the time a title reaches here it's already gone
        # through this app's own sanitize() (meta.py), which turns "'" into
        # a space the same way, so there's no "'" character left to tell
        # the two apart. Try it both ways: drop the lone letter (today's
        # behavior) or fuse it back onto the previous word, and accept
        # either normalization as a match.
        CONTRACTION_REMNANTS = {"s", "t", "d", "m"}

        def filter_1_letter(s, fuse=False):
            words = s.split()
            result = []
            for w in words:
                if len(w) > 1 or w.isdigit():
                    result.append(w)
                elif fuse and w in CONTRACTION_REMNANTS and result:
                    result[-1] += w
                # else: single-letter noise - drop it.
            return " ".join(result)

        sortkeys = item.get("sortkeys", {})
        if not isinstance(sortkeys, dict):
            sortkeys = {}
        ptn_title = sortkeys.get("title", "")

        ALLOWED_EXTRAS = {
            "filme", "movie", "series", "serie", "temporada", "season",
            "pt", "br", "dublado", "legendado", "dual", "audio", "remastered",
            "remaster", "director", "cut", "extended", "unrated", "edition",
            "part", "parte", "vol", "volume", "ep", "episodio", "1080p", "4k",
            "2160p", "720p", "hd", "web", "dl", "bluray", "remux", "tv",
            "h264", "h265", "hevc", "avc", "aac", "ddp", "atmos", "x264", "x265",
            "amzn", "nf", "dsnp", "max", "hbo", "peacock", "hulu", "apple", "appletv",
            "bioma", "c76", "lapumia", "wolverdon", "bludv", "comandotorrents", "comando",
            "torrent", "torrents", "yts", "yify", "rarbg", "rmteam", "mkv", "mp4", "avi"
        }

        titles = getattr(self.strm_meta, 'titles', [])
        if not titles:
            return False

        def title_matches(title, fuse):
            # Match only against the title portion PTN parsed out of the
            # filename (everything before the year/SxxEyy marker), not the
            # raw filename as a whole. Beyond that marker there's often an
            # episode title, quality tags, or a release group, and a
            # short/generic search title can spuriously match a word that
            # only appears there rather than in the actual title (e.g. a
            # show titled "Dark" matching some other show's
            # "...S03E03.A.Dark.Web..." episode). Fall back to the raw
            # filename if PTN couldn't find a title at all.
            file_clean = clean_str(ptn_title or file_name_raw)
            file_clean_filtered = filter_1_letter(file_clean, fuse)

            title_clean = clean_str(str(title))
            raw_words = title_clean.split()

            if len(raw_words) <= 2:
                # Very short titles: match the literal phrase, keeping any
                # single-letter word (e.g. the "D" in "Dia D"). Dropping it
                # would collapse a distinctive short title into a far more
                # common word ("Dia D" -> just "dia") that matches almost
                # any file containing that word anywhere, adjacent or not.
                title_for_match = title_clean
                file_for_match = file_clean
            else:
                title_for_match = filter_1_letter(title_clean, fuse)
                file_for_match = file_clean_filtered
                if not title_for_match:
                    title_for_match = title_clean
                    file_for_match = file_clean

            words = title_for_match.split()
            strong_words = [w for w in words if w not in STOP_WORDS]
            if not strong_words: strong_words = words

            is_match_candidate = False

            if len(raw_words) <= 2:
                if f" {title_for_match} " in f" {file_for_match} ":
                    is_match_candidate = True
                else:
                    pattern = r'\b' + re.escape(title_for_match) + r'\b'
                    if re.search(pattern, file_for_match):
                        is_match_candidate = True
            else:
                file_tokens = set(file_for_match.split())
                missing = [w for w in strong_words if w not in file_tokens]

                if not missing or (len(strong_words) >= 4 and len(missing) <= 1):
                    is_match_candidate = True

            if not is_match_candidate:
                return False

            if ptn_title:
                ptn_clean = clean_str(ptn_title)
                ptn_filtered = filter_1_letter(ptn_clean, fuse)
                ptn_strong = [w for w in ptn_filtered.split() if w not in STOP_WORDS]
                meaningful_extras = [w for w in ptn_strong if w not in strong_words and w not in ALLOWED_EXTRAS]

                if len(meaningful_extras) > 0:
                    return False

            return True

        for title in titles:
            if title_matches(title, False) or title_matches(title, True):
                return True

        return False

    def get_title(self, res_raw):
        file_name = str(self.item.get("name", "Unknown"))
        name_upper = file_name.upper()
        
        try:
            file_size_raw = self.item.get("size", 0)
            file_size = hr_size(int(file_size_raw)) if file_size_raw else "0B"
        except Exception:
            file_size = "0B"

        # Codec
        if any(x in name_upper for x in ["AV1", "AV01"]): codec = "AV1"
        elif any(x in name_upper for x in ["HEVC", "X265", "H265", "H.265"]): codec = "H.265"
        elif any(x in name_upper for x in ["AVC", "X264", "H264", "H.264"]): codec = "H.264"
        else: 
            sortkeys = getattr(self.parsed, 'sortkeys', {})
            codec = sortkeys.get("codec", "CODEC?") if isinstance(sortkeys, dict) else "CODEC?"

        # --- NOVO: Captura o serviço de Streaming ---
        streaming = ""
        if re.search(r'\bNF\b', name_upper): streaming = "NF"
        elif re.search(r'\b(AMZN|AMAZON)\b', name_upper): streaming = "AMZN"
        elif re.search(r'\bDSNP\b', name_upper): streaming = "DSNP"
        elif re.search(r'\b(HMAX|MAX)\b', name_upper): streaming = "MAX"
        elif re.search(r'\bATVP\b', name_upper): streaming = "ATVP"
        elif re.search(r'\bPMTP\b', name_upper): streaming = "PMTP"
        elif re.search(r'\bHULU\b', name_upper): streaming = "HULU"
        elif re.search(r'\bPEAC\b', name_upper): streaming = "PEAC"
        elif re.search(r'\bCR\b', name_upper): streaming = "CR"
        elif re.search(r'\b(IT|ITUNES)\b', name_upper): streaming = "iT"
        
        # Formata para adicionar no layout apenas se encontrou algum streaming
        stream_display = f"   📺 {streaming}" if streaming else ""

        # HDR / DV (Exatamente como o Nuvio pede nas Regex)
        hdr_list = []
        if "HDR10+" in name_upper or "HDR+" in name_upper:
            hdr_list.append("HDR10+")
        elif "HDR10" in name_upper:
            hdr_list.append("HDR10")
        elif "HDR" in name_upper:
            hdr_list.append("HDR")   
        if "DV" in name_upper or "DOLBY VISION" in name_upper:
            hdr_list.append("DV")
        hdr_display = " ".join(hdr_list) if hdr_list else "SDR"

        # Audio (Siglas exatas para ativar os combos do Nuvio)
        audio_codec = ""
        if "ATMOS" in name_upper: 
            audio_codec = "Atmos"
        elif any(x in name_upper for x in ["TRUEHD", "TRUE-HD"]):
            audio_codec = "TrueHD"
        elif any(x in name_upper for x in ["DDP", "DD+", "EAC3", "DIGITAL PLUS"]): 
            audio_codec = "DD+"
        elif any(x in name_upper for x in ["DD", "AC3", "DOLBY DIGITAL"]): 
            audio_codec = "DD"
        elif "DTS-HD MA" in name_upper or "DTSHD-MA" in name_upper or "DTSHDMA" in name_upper:
            audio_codec = "DTS-HD MA"
        elif "DTS-HD" in name_upper or "DTSHD" in name_upper:
            audio_codec = "DTS-HD"
        elif "DTS" in name_upper: 
            audio_codec = "DTS"
        elif "AAC" in name_upper: 
            audio_codec = "AAC"
        else:
            audio_codec = "Audio"

        channels = ""
        channel_match = re.search(r'\b(7\.1|5\.1|2\.0)\b', file_name)
        if not channel_match: channel_match = re.search(r'(7\.1|5\.1|2\.0)', file_name)
        if channel_match: channels = f" {channel_match.group(1)}"
        
        audio_final = f"{audio_codec}{channels}".strip()

        # Quality + Prefixos do Nuvio (Ativa os emblemas Best/Good/OK)
        quality = "WEB-DL"
        prefix = "⭑"
        
        if "REMUX" in name_upper: 
            quality = "Remux"
            prefix = "♛"
        elif "BLURAY" in name_upper: 
            quality = "BluRay"
            prefix = "⭑"
        elif "HDTV" in name_upper: 
            quality = "HDTV"
            prefix = "△"
        elif "WEBRIP" in name_upper: 
            quality = "WebRip"
            prefix = "△"

        # Resolução na descrição para o Nuvio mapear
        res_lower = str(res_raw).lower()
        if "2160" in res_lower or "4k" in res_lower: res_display = "2160p"
        elif "1080" in res_lower: res_display = "1080p"
        elif "720" in res_lower: res_display = "720p"
        else: res_display = "SD"

        # Nome Limpo (Fallback via PTN)
        keys = getattr(self.parsed, 'sortkeys', {})
        if not isinstance(keys, dict): keys = {}
        title_clean = keys.get("title") or "Titulo"

        # 1. Título PT-BR vem do nome principal do metadado (IMDb/TMDB).
        # Só cai pro título extraído do nome do arquivo se a metadata não
        # trouxe nada - isso evita mostrar lixo tipo "DV" quando o nome do
        # arquivo não tem um título de verdade (ex: foi achado só pelo ID).
        titulo_pt = getattr(self.strm_meta, 'name', None) or title_clean

        # 2. Tenta pegar o título original dos atributos diretos
        titulo_original = getattr(self.strm_meta, 'original_title', None) or ''

        # 3. Se não veio atributo direto (ou se veio igual ao PT), procura na lista de 'titles' alternativos
        if not titulo_original or str(titulo_original).lower() == str(titulo_pt).lower():
            titulos_alt = getattr(self.strm_meta, 'titles', [])
            if isinstance(titulos_alt, list):
                for t in titulos_alt:
                    if t and str(t).lower() != str(titulo_pt).lower():
                        titulo_original = str(t)
                        break
        
        # 4. Se ainda estiver vazio ou igual, usa o título extraído do nome do arquivo
        if not titulo_original or str(titulo_original).lower() == str(titulo_pt).lower():
            if title_clean and str(title_clean).lower() != str(titulo_pt).lower():
                titulo_original = title_clean
            else:
                titulo_original = titulo_pt # Último recurso: repete o título PT

        ano_meta = getattr(self.strm_meta, 'year', keys.get("year", ""))

        if getattr(self.strm_meta, 'type', '') == "series":
            try:
                s = int(keys.get("season", keys.get("se", 0)))
                e = int(keys.get("episode", keys.get("ep", 0)))
                sufixo = f"– S{s:02}E{e:02}"
            except (TypeError, ValueError):
                sufixo = ""
            
            linha_pt = f"🎬 {titulo_pt} {sufixo}".strip()
            linha_orig = f"🌐 {titulo_original} {sufixo}".strip()
        else:
            ano_str = f" - ({ano_meta})" if ano_meta else ""
            linha_pt = f"🎬 {titulo_pt}{ano_str}".strip()
            linha_orig = f"🌐 {titulo_original}{ano_str}".strip()

        # LAYOUT: Construído com o streaming na primeira linha
        line1 = f"💎 {res_display} {hdr_display}   🔊 {audio_final}{stream_display}".strip()
        line2 = f"💿 {quality}   ⚙️ {codec}   💾 {file_size}"
        line3 = f"{linha_pt}"

        return f"{line3}\n{line1}\n{line2}"

    def get_proxy_url(self):
        file_id = str(self.item.get("id", ""))
        file_name = urllib.parse.quote(str(self.item.get("name", ""))) or "file_name.vid"
        if "behaviorHints" not in self.constructed:
             self.constructed["behaviorHints"] = {}
        self.constructed["behaviorHints"]["proxyHeaders"] = {
            "request": {"Server": "Stremio"}
        }
        return f"{self.proxy_url}/load/{file_id}/{file_name}"

    def get_gapi_url(self):
        file_id = str(self.item.get("id", ""))
        file_name = urllib.parse.quote(str(self.item.get("name", ""))) or "file_name.vid"
        if "behaviorHints" not in self.constructed:
             self.constructed["behaviorHints"] = {}
        self.constructed["behaviorHints"]["proxyHeaders"] = {
            "request": {"Authorization": f"Bearer {getattr(self, 'acc_token', '')}"}
        }
        return f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&file_name={file_name}"

    def construct_stream(self):
        self.constructed = {}
        self.constructed["behaviorHints"] = {}
        self.constructed["behaviorHints"]["notWebReady"] = True
        
        keys = getattr(self.parsed, 'sortkeys', {})
        if not isinstance(keys, dict): keys = {}
        res_raw = str(keys.get("res", ""))
        self.constructed["behaviorHints"]["bingeGroup"] = f"gdrive-{res_raw}"

        res_lower = res_raw.lower()
        if "2160" in res_lower: res_nome_topo = "[4k]"
        elif "1080" in res_lower: res_nome_topo = "[Full HD]"
        elif "720" in res_lower: res_nome_topo = "[HD]"
        else: res_nome_topo = res_raw or "[SD]"

        self.constructed["filename"] = str(self.item.get("name", ""))
        self.constructed["url"] = self.get_url()
        self.constructed["name"] = f"▶️ Stream Helium {res_nome_topo} 🇧🇷"
        self.constructed["title"] = self.get_title(res_raw)
        self.constructed["sortkeys"] = keys

        return self.constructed

    def best_res(self, item):
        try:
            score = 0
            file_name = str(item.get("filename", "")).upper()
            sortkeys = item.get("sortkeys", {})
            if not isinstance(sortkeys, dict): sortkeys = {}

            # 1. Resolução
            res_raw = str(sortkeys.get("res", "")).upper()
            if "2160" in res_raw or "4K" in res_raw or "2160P" in file_name or "4K" in file_name: score += 1000000000
            elif "1080" in res_raw or "FHD" in res_raw or "1080P" in file_name: score += 800000000
            elif "720" in res_raw or "HD" in res_raw or "720P" in file_name: score += 600000000
            else: score += 400000000

            # 2. Fonte
            if "REMUX" in file_name: score += 100000000
            elif "BLURAY" in file_name: score += 80000000
            elif "WEB-DL" in file_name or "WEBDL" in file_name: score += 60000000

            # 3. HDR e Áudio 
            if "DV" in file_name or "DOLBY VISION" in file_name: score += 10000000
            if "HDR10+" in file_name or "HDR+" in file_name: score += 8000000
            if "ATMOS" in file_name: score += 1000000
            elif "DDP" in file_name or "DD+" in file_name: score += 800000

            # 4. Idioma
            if any(x in file_name for x in ["DUBLADO", "PT-BR", "PTBR", "DUAL", "MULTI"]): score += 1000

            return score
        except Exception as e:
            logger.warning("Failed to score item: %s", e)
            return 1
