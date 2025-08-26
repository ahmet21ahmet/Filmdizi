import asyncio
import aiohttp
import re
import os
from urllib.parse import urljoin, quote_plus
from bs4 import BeautifulSoup
import logging
import time

# --- Otomatik Çeviri Kütüphanesi ---
try:
    from googletrans import Translator
except ImportError:
    print("Lütfen 'googletrans' kütüphanesini kurun: pip install googletrans==4.0.0-rc1")
    exit()

# --- Logging Ayarları ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Temel Ayarlar ---
BASE_URL = "https://dizifun5.com/filmler"
PROXY_BASE = "https://3.nejyoner19.workers.dev/?url="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.8,en-US;q=0.5,en;q=0.3",
}

# --- GÜNCELLENDİ: TMDB API Ayarları ---
# API Anahtarı artık GitHub Secrets'tan okunacak.
TMDB_API_KEY = os.getenv('TMDB_API_KEY')
TMDB_API_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

# --- Çevirmen Nesnesi ---
translator = Translator()

# --- (Diğer tüm fonksiyonlar önceki kod ile aynıdır) ---
# ...
# Bu kısımda önceki yanıtta bulunan create_proxy_url, sanitize_id, fix_url,
# fetch_page, translate_text_sync, get_tmdb_data, clean_movie_title,
# get_movie_site_data, extract_m3u8_from_movie, get_all_movie_links
# fonksiyonları yer almaktadır. Kodun bütünlüğü için bu fonksiyonların
# script dosyanızda olduğundan emin olun.
# ...

# --- Önceki koddan kopyalanacak fonksiyonlar ---
def create_proxy_url(original_url):
    if not original_url: return None
    if PROXY_BASE in original_url: return original_url
    return f"{PROXY_BASE}{original_url}"

def sanitize_id(text):
    if not text: return "UNKNOWN"
    turkish_chars = {'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G', 'ı': 'i', 'I': 'I', 'İ': 'I', 'ö': 'o', 'Ö': 'O', 'ş': 's', 'Ş': 'S', 'ü': 'u', 'Ü': 'U'}
    for tr, en in turkish_chars.items(): text = text.replace(tr, en)
    import unicodedata
    text = unicodedata.normalize('NFD', text).encode('ascii', 'ignore').decode('utf-8')
    text = re.sub(r'[^A-Za-z0-9\s]', '', text)
    text = re.sub(r'\s+', '_', text.strip()).upper()
    return text if text else "UNKNOWN"

def fix_url(url, base=BASE_URL):
    if not url: return None
    if url.startswith('/'): return urljoin(base, url)
    return url

def translate_text_sync(text, dest_lang='tr'):
    if not text or not isinstance(text, str): return ""
    try:
        translated = translator.translate(text, dest=dest_lang)
        return translated.text
    except Exception as e:
        logger.error(f"Çeviri hatası: {e}")
        return text

async def get_tmdb_data(session, turkish_title, original_title=None):
    if not TMDB_API_KEY:
        logger.error("[HATA] TMDB_API_KEY ortam değişkeni bulunamadı!")
        return None
    
    search_titles = [turkish_title]
    if original_title and original_title.lower() != turkish_title.lower():
        search_titles.append(original_title)

    movie_id = None
    for title_to_search in search_titles:
        try:
            encoded_title = quote_plus(title_to_search)
            search_url = f"{TMDB_API_URL}/search/movie?api_key={TMDB_API_KEY}&query={encoded_title}&language=tr-TR"
            search_results = await fetch_page(session, search_url, is_json=True)
            if search_results and search_results.get('results'):
                movie_id = search_results['results'][0]['id']
                logger.info(f"[TMDB] Sonuç bulundu: '{title_to_search}' -> ID: {movie_id}")
                break
        except Exception: continue
    
    if not movie_id: return None

    try:
        details_url_tr = f"{TMDB_API_URL}/movie/{movie_id}?api_key={TMDB_API_KEY}&language=tr-TR&append_to_response=credits"
        details = await fetch_page(session, details_url_tr, is_json=True)
        if not details: return None

        overview = details.get('overview', '').strip()

        if not overview:
            logger.info(f"[Çeviri] Türkçe açıklama yok. İngilizce kaynak aranıyor... (ID: {movie_id})")
            details_url_en = f"{TMDB_API_URL}/movie/{movie_id}?api_key={TMDB_API_KEY}&language=en-US"
            details_en = await fetch_page(session, details_url_en, is_json=True)
            if details_en and details_en.get('overview'):
                english_overview = details_en.get('overview')
                loop = asyncio.get_event_loop()
                overview = await loop.run_in_executor(None, translate_text_sync, english_overview)

        genres = [g['name'] for g in details.get('genres', [])]
        cast = [a['name'] for a in details.get('credits', {}).get('cast', [])[:5]]
        director = next((p['name'] for p in details.get('credits', {}).get('crew', []) if p['job'] == 'Director'), 'Bilinmiyor')
        
        return {
            'overview': overview or "Açıklama bulunamadı.", 'year': details.get('release_date', '----').split('-')[0],
            'genres': ", ".join(genres), 'cast': ", ".join(cast), 'director': director,
            'poster_url': f"{TMDB_IMAGE_BASE_URL}{details.get('poster_path')}" if details.get('poster_path') else "",
        }
    except Exception as e:
        logger.error(f"[!] TMDB detay çekme hatası (ID: {movie_id}): {e}")
        return None

def clean_movie_title(title):
    if not title: return ""
    title = re.sub(r'\s*\(\d{4}\)\s*', '', title)
    patterns = ["türkçe dublaj", "tr dublaj", "altyazılı", "full hd", "1080p", "720p", "izle"]
    for p in patterns: title = re.sub(p, '', title, flags=re.IGNORECASE)
    return title.strip()

async def get_movie_site_data(session, movie_url):
    content = await fetch_page(session, movie_url)
    if not content: return None, None, None
    soup = BeautifulSoup(content, 'html.parser')
    tr_title = clean_movie_title(soup.select_one(".text-bold").get_text(strip=True) if soup.select_one(".text-bold") else "Bilinmeyen Film")
    org_title_el = soup.select_one(".uk-text-muted.uk-text-small.uk-margin-small-top")
    org_title = org_title_el.get_text(strip=True) if org_title_el else None
    logo_el = soup.select_one(".media-cover img")
    logo_url = fix_url(logo_el.get("src")) if logo_el else ""
    return tr_title, org_title, logo_url

async def extract_m3u8_from_movie(session, movie_url):
    content = await fetch_page(session, movie_url)
    if not content: return None
    soup = BeautifulSoup(content, 'html.parser')
    iframe = soup.select_one('iframe[src*="premiumvideo.click"]')
    if iframe:
        src = fix_url(iframe.get("src", ""))
        match = re.search(r'(?:/player/|/e/|file_id=)([a-zA-Z0-9]+)', src)
        if match:
            file_id = match.group(1)
            return f"https://d2.premiumvideo.click/uploads/encode/{file_id}/master.m3u8"
    return None

async def get_all_movie_links():
    async with aiohttp.ClientSession() as session:
        all_links = set()
        page_num = 1
        while True:
            content = await fetch_page(session, f"{BASE_URL}?p={page_num}")
            if not content: break
            soup = BeautifulSoup(content, 'html.parser')
            links = {fix_url(a['href']) for a in soup.select("a.uk-position-cover[href*='/film/']")}
            if not links: break
            all_links.update(links)
            if not soup.select_one(".uk-pagination .uk-pagination-next a"): break
            page_num += 1
            await asyncio.sleep(0.3)
        logger.info(f"[✓] Toplam {len(all_links)} benzersiz film linki toplandı.")
        return sorted(list(all_links))

async def process_movies(all_movie_links, output_filename="filmler.m3u"):
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        with open(output_filename, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            semaphore = asyncio.Semaphore(5)
            
            async def process_single_movie(movie_url):
                async with semaphore:
                    try:
                        tr_title, org_title, logo_url = await get_movie_site_data(session, movie_url)
                        if not tr_title: return None
                        logger.info(f"\n[+] İşleniyor: {tr_title} (Orijinal: {org_title or 'Yok'})")
                        
                        m3u8_url = await extract_m3u8_from_movie(session, movie_url)
                        if not m3u8_url: return None
                        
                        tmdb_info = await get_tmdb_data(session, tr_title, org_title)
                        
                        final_data = {'title': tr_title, 'logo_url': logo_url, 'm3u8_url': create_proxy_url(m3u8_url)}
                        if tmdb_info:
                            final_data.update(tmdb_info)
                            if tmdb_info.get('poster_url'): final_data['logo_url'] = tmdb_info['poster_url']
                        else:
                            final_data.update({'year': '????', 'genres': 'Bilinmiyor', 'overview': 'Açıklama yok.', 'cast': '', 'director': ''})
                        return final_data
                    except Exception as e:
                        logger.error(f"[!] Film işleme hatası ({movie_url}): {e}")
                        return None
            
            tasks = [process_single_movie(url) for url in all_movie_links]
            results = await asyncio.gather(*tasks)
            
            successful_count = 0
            for result in filter(None, results):
                group = result['genres'].split(',')[0].strip() if result['genres'] != 'Bilinmiyor' else 'Filmler'
                f.write(f'#EXTINF:-1 tvg-id="{sanitize_id(result["title"])}" tvg-name="{result["title"]} ({result["year"]})" tvg-logo="{result["logo_url"]}" group-title="{group}",{result["title"]}\n')
                f.write(f'#EXTVLCOPT:description={result["overview"].replace(""', "'")} | Yönetmen: {result["director"]} | Oyuncular: {result["cast"]}\n')
                f.write(result["m3u8_url"].strip() + "\n")
                logger.info(f"[✓] {result['title']} eklendi.")
                successful_count += 1

            logger.info(f"\n[✓] {successful_count} film başarıyla eklendi.")
    logger.info(f"\n[✓] {output_filename} dosyası oluşturuldu.")

async def main():
    start_time = time.time()
    # Çıktı dosyasının adını sabitliyoruz
    output_file = "filmler.m3u"
    
    movie_urls = await get_all_movie_links()
    if movie_urls:
        await process_movies(movie_urls, output_filename=output_file)
    else:
        logger.error("[!] Hiç film linki bulunamadı.")
    end_time = time.time()
    logger.info(f"\n[✓] Tüm işlemler tamamlandı. Süre: {end_time - start_time:.2f} saniye")

if __name__ == "__main__":
    if not os.getenv('TMDB_API_KEY'):
        print("[HATA] Başlamadan önce TMDB_API_KEY ortam değişkenini ayarlamanız gerekiyor.")
    else:
        asyncio.run(main())
