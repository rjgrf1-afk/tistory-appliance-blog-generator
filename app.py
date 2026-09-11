import streamlit as st
import requests
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import json, re, random, html, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

st.set_page_config(
    page_title="티스토리 가전리뷰 생성기",
    page_icon="✍️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 860px; }
    .stButton > button { border-radius: 12px; font-weight: bold; }
    .stTextInput > div > input { border-radius: 10px; }
    .stTextArea > div > textarea { border-radius: 10px; font-family: 'Malgun Gothic', sans-serif; }
    h1 { font-size: 1.6rem !important; }
    h2 { font-size: 1.2rem !important; color: #2563EB; }
    h3 { font-size: 1.05rem !important; background: #EFF6FF; padding: 8px 14px; border-radius: 8px; color: #1D4ED8; }
    .stRadio > div { flex-wrap: wrap; gap: 6px; }
    [data-testid="stSidebar"] { display: none; }
</style>
""", unsafe_allow_html=True)

RSS_URLS = {
    "생활가전":   "https://news.google.com/rss/search?q=무선청소기+공기청정기+가습기+신제품&hl=ko&gl=KR&ceid=KR:ko",
    "주방가전":   "https://news.google.com/rss/search?q=에어프라이어+커피머신+주방가전+신제품&hl=ko&gl=KR&ceid=KR:ko",
    "모바일IT":  "https://news.google.com/rss/search?q=노트북+이어폰+스마트워치+신제품&hl=ko&gl=KR&ceid=KR:ko",
    "사무게이밍": "https://news.google.com/rss/search?q=모니터+키보드+마우스+추천&hl=ko&gl=KR&ceid=KR:ko",
}

# 계절가전은 검색어 자체가 현재 달(月)에 따라 바뀌어야 해서 별도 함수로 처리
SEASONAL_KEYWORDS = {
    (12, 1, 2): ("난방기+온풍기+전기매트+겨울가전+추천", "전기매트 고르는 법"),
    (3, 4, 5):  ("제습기+공기청정기+이불건조기+봄가전+추천", "공기청정기 고르는 법"),
    (6, 7, 8):  ("선풍기+에어컨+서큘레이터+여름가전+추천", "서큘레이터 고르는 법"),
    (9, 10, 11): ("가습기+온열기+전기매트+환절기가전+추천", "가습기 고르는 법"),
}

def _seasonal_keyword_and_default():
    month = datetime.now().month
    for months, (keyword, default_topic) in SEASONAL_KEYWORDS.items():
        if month in months:
            return keyword, default_topic
    return "계절가전+추천", "계절가전 고르는 법"

def _seasonal_rss_url():
    keyword, _ = _seasonal_keyword_and_default()
    return f"https://news.google.com/rss/search?q={keyword}&hl=ko&gl=KR&ceid=KR:ko"

def _rss_url_for(category):
    if category == "계절가전":
        return _seasonal_rss_url()
    return RSS_URLS.get(category, "")

# 스톡사진 alt 텍스트에 사람이 언급되면 제외 (제품만 나오는 사진 위주로 필터링)
NO_PEOPLE_PATTERN = re.compile(
    r'\b(woman|women|man|men|male|female|girl|boy|kid|kids|child|children|family|families|'
    r'person|people|hand|hands|model|portrait|face|someone|guy|guys|lady|ladies|baby|babies|'
    r'human|gamer|gamers|worker|workers|cleaners|team|professional|adult|teen|couple|friend|'
    r'friends|student|customer)\b',
    re.IGNORECASE,
)

def _filter_photos(photos, query, exclude_people):
    """alt 텍스트에 검색어가 실제로 포함된 사진만 남기고, exclude_people이면 사람 나오는 사진도 제외"""
    keywords = [w.lower().strip('.,') for w in query.split() if len(w) >= 3]
    kept = []
    for p in photos:
        alt = (p.get('alt', '') or '')
        if exclude_people and NO_PEOPLE_PATTERN.search(alt):
            continue
        if keywords and not any(k in alt.lower() for k in keywords):
            continue
        kept.append(p)
    return kept

THEMES = {
    "생활가전":   ((6, 58, 42),  (10, 100, 70),  (60, 220, 160)),
    "주방가전":   ((90, 24, 10), (150, 55, 14),  (255, 150, 60)),
    "모바일IT":  ((8, 14, 75),  (28, 42, 148),  (65, 182, 255)),
    "사무게이밍": ((22, 8, 58),  (90, 14, 130),  (210, 90, 255)),
    "계절가전":   ((6, 40, 90),  (14, 90, 160),  (110, 200, 255)),
}


def get_font(size):
    paths = [
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "C:/Windows/Fonts/malgunbd.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def strip_emoji(text):
    return re.sub(
        r'[\U00010000-\U0010ffff\U00002600-\U000027BF\U0001F300-\U0001F9FF]',
        '', text).strip()


def get_trending_topic(category):
    try:
        resp = requests.get(_rss_url_for(category), timeout=10,
                            headers={'User-Agent': 'Mozilla/5.0'})
        root = ET.fromstring(resp.content)
        # 5일 이내 기사만 후보로 사용 — 오래된 기사가 계절과 안 맞는 주제로 뽑히는 것 방지
        cutoff = datetime.now(timezone.utc) - timedelta(days=5)
        titles = []
        for item in root.findall('.//item')[:15]:
            date_el = item.find('pubDate')
            if date_el is not None and date_el.text:
                try:
                    if parsedate_to_datetime(date_el.text) < cutoff:
                        continue
                except Exception:
                    pass
            el = item.find('title')
            if el is not None and el.text:
                t = el.text.split(' - ')[0].strip()
                if len(t) > 5:
                    titles.append(t)
        if titles:
            return random.choice(titles[:5])
    except Exception:
        pass
    _, seasonal_default = _seasonal_keyword_and_default()
    defaults = {
        "생활가전": "무선청소기 고르는 법", "주방가전": "에어프라이어 고르는 법",
        "모바일IT": "노트북 고르는 기준", "사무게이밍": "모니터 고르는 법",
        "계절가전": seasonal_default,
    }
    return defaults.get(category, "가전제품 고르는 법")


def get_news_context(topic, category):
    def clean(text):
        if not text:
            return ""
        text = re.sub(r'<[^>]+>', ' ', text)
        text = html.unescape(text)
        return re.sub(r'\s+', ' ', text).strip()

    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    articles = []
    for url in [
        f"https://news.google.com/rss/search?q={urllib.parse.quote(topic)}&hl=ko&gl=KR&ceid=KR:ko",
        _rss_url_for(category),
    ]:
        if not url or len(articles) >= 5:
            break
        try:
            resp = requests.get(url, timeout=12, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(resp.content)
            for item in root.findall('.//item'):
                if len(articles) >= 5:
                    break
                t_el    = item.find('title')
                d_el    = item.find('description')
                date_el = item.find('pubDate')
                src_el  = item.find('source')
                if date_el is not None and date_el.text:
                    try:
                        if parsedate_to_datetime(date_el.text) < cutoff:
                            continue
                    except Exception:
                        pass
                t = clean(t_el.text if t_el is not None else "")
                if len(t) > 5:
                    articles.append({
                        'title': t.split(' - ')[0].strip(),
                        'description': clean(d_el.text if d_el is not None else "")[:600],
                        'date': (date_el.text or "")[:30] if date_el is not None else "",
                        'source': src_el.text if src_el is not None else "",
                    })
        except Exception:
            continue
    return articles


def fetch_stock_photos(pexels_key, query, count):
    if not pexels_key or not query or count <= 0:
        return []
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 25, "orientation": "landscape"},
            headers={"Authorization": pexels_key},
            timeout=12,
        )
        photos = resp.json().get("photos", [])
        # 사람 없는 사진 우선, 부족하면 배너보다는 사람 나오는 사진으로 채움
        candidates = _filter_photos(photos, query, exclude_people=True)
        if len(candidates) < count:
            seen = {p.get('id') for p in candidates}
            for p in _filter_photos(photos, query, exclude_people=False):
                if p.get('id') not in seen:
                    candidates.append(p)
                    seen.add(p.get('id'))
                if len(candidates) >= count:
                    break
        images = []
        for p in candidates[:count]:
            url = p.get("src", {}).get("large")
            if not url:
                continue
            img_resp = requests.get(url, timeout=12)
            images.append(img_resp.content)
        return images
    except Exception:
        return []


def generate_content(category, topic, api_key, articles):
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-3.5-flash-lite')

    if articles:
        news_block = ""
        for i, a in enumerate(articles, 1):
            news_block += f"\n[뉴스 {i}]\n제목: {a['title']}\n"
            if a['date']:
                news_block += f"날짜: {a['date']}\n"
            if a['source']:
                news_block += f"출처: {a['source']}\n"
            if a['description']:
                news_block += f"내용: {a['description']}\n"
    else:
        news_block = "(수집된 뉴스 없음 — 일반적인 구매 기준·상식 수준에서 신중하게 작성)"

    intro_styles = [
        "독자가 가장 궁금해하는 핵심 질문을 던지며 시작 (예: '요즘 이런 고민 많으시죠?')",
        "놀라운 사실이나 수치로 시작해 독자의 호기심 유발",
        "독자의 공감을 이끄는 일상적인 상황 묘사로 시작 (예: 구매 전 흔히 하는 실수)",
        "결론(핵심 기준)을 먼저 던지고 '그 이유는 이렇습니다' 식으로 시작",
        "최근 가장 많이 검색되는 이유를 중심으로 시작",
    ]
    section_styles = [
        "각 소제목은 독자에게 말 걸듯 질문형으로",
        "각 소제목은 핵심 키워드 + 감탄·놀람 표현으로",
        "각 소제목은 '~하려면?', '~한 이유는?' 처럼 이유·방법형으로",
        "각 소제목은 순서나 단계를 암시하는 형태로",
    ]

    prompt = f"""당신은 정직하고 꼼꼼하게 정보를 전달하는 IT·가전 제품 구매 가이드 블로거입니다.
과장 광고 없이, 실제 소비자에게 도움이 되는 선택 기준을 알려주는 것이 목표입니다.
아래 뉴스 기사들을 사실 확인 자료로만 참고하여, 완전히 새롭게 쓴 독창적인 블로그 글을 JSON 형식으로만 작성하세요.
JSON 외 텍스트는 절대 포함하지 마세요.

━━━ 참고 뉴스 (사실 확인용) ━━━
{news_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

카테고리: {category} / 주제: {topic}

[이번 글의 스타일]
- 서론 방식: {random.choice(intro_styles)}
- 소제목 방식: {random.choice(section_styles)}

[절대 규칙]
1. 모든 문장은 "~입니다", "~합니다" 어투 통일 / 질문은 "~일까?" 처럼 친근하게
2. 뉴스 문장 절대 복사 금지 — 같은 사실도 완전히 새로운 문장으로 재작성
3. 뉴스에서 확인된 사실(브랜드명·출시일·행사명 등)만 구체적으로 언급 / 정확한 가격·세부 스펙 수치·단종 여부처럼 AI가 확신할 수 없는 정보는 절대 지어내지 말고 "정확한 가격과 최신 스펙은 구매처에서 꼭 확인하세요" 식으로 안내
4. 특정 제품을 "무조건 1위·최고"라고 단정하지 말 것 — "이런 용도·공간에는 이런 기준이 중요합니다"처럼 선택 기준과 비교 관점 중심으로 서술
5. 각 섹션 최소 200자 이상 / 실제 사용 팁·체크리스트 위주로 풍부하게
6. 특정 행정구역(구·동·시·군) 사용 금지 — 전국 독자 대상
7. 제목은 30~40자 이내로 간결하게, "고르는 법"·"체크리스트"·"비교"·"가이드"·"추천 기준" 같은 검색 키워드를 자연스럽게 포함
8. image_query_en은 스톡사진 검색에 쓸 영어 키워드 1~2단어 — 브랜드명·모델명 없이 제품 카테고리만 (예: humidifier, wireless vacuum cleaner, gaming monitor)

{{"title": "30~40자 이내, 구매가이드 키워드 포함, 매번 다르게",
  "intro": "4~5문장, 위 서론 방식 적용",
  "sections": [
    {{"title": "이모지 + 소제목1", "content": "5~7문장, 실용적 체크리스트 위주"}},
    {{"title": "이모지 + 소제목2", "content": "5~7문장, 실용적 체크리스트 위주"}},
    {{"title": "이모지 + 소제목3", "content": "5~7문장, 실용적 체크리스트 위주"}}
  ],
  "conclusion": "핵심 요약 마무리 3~4문장",
  "faq": [
    {{"q": "구매 전 궁금할 질문1?", "a": "답변 2~3문장"}},
    {{"q": "구매 전 궁금할 질문2?", "a": "답변 2~3문장"}},
    {{"q": "구매 전 궁금할 질문3?", "a": "답변 2~3문장"}}
  ],
  "tags": "태그1, 태그2, 태그3, 태그4, 태그5, 태그6, 태그7, 태그8, 태그9, 태그10",
  "image_query_en": "스톡사진 검색용 영어 키워드 1~2단어"
}}"""

    response = model.generate_content(prompt, generation_config={"temperature": 0.8})
    text = response.text.strip()
    if '```' in text:
        text = re.sub(r'```(?:json)?', '', text).replace('```', '').strip()
    return json.loads(text)


def generate_image(category, section_title, base_topic, idx):
    W, H = 800, 400
    c1, c2, acc = THEMES.get(category, ((28, 28, 58), (58, 58, 98), (148, 148, 255)))

    img = Image.new('RGB', (W, H))
    draw = ImageDraw.Draw(img)
    for x in range(W):
        t = x / (W - 1)
        col = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
        draw.line([(x, 0), (x, H)], fill=col)

    ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for y in range(H):
        od.line([(0, y), (W, y)], fill=(0, 0, 0, int((y / H) * 60)))
    od.ellipse([W - 320, -100, W + 100, H + 100], fill=(*acc, 28))
    od.ellipse([W - 180, H - 180, W + 60, H + 60], fill=(*acc, 18))
    od.ellipse([-60, H - 180, 200, H + 60], fill=(*acc, 14))
    img = Image.alpha_composite(img.convert('RGBA'), ov).convert('RGB')
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, H - 10, W, H], fill=acc)
    draw.rectangle([0, 0, 8, H], fill=acc)

    font_badge = get_font(16)
    font_title = get_font(34)
    font_sub   = get_font(18)

    cat_text = category
    bx1, by1 = 28, 26
    bx2 = bx1 + int(len(cat_text) * 13) + 26
    by2 = by1 + 34
    draw.rounded_rectangle([bx1, by1, bx2, by2], radius=17, fill=acc)
    draw.text(((bx1 + bx2) // 2, (by1 + by2) // 2), cat_text,
              font=font_badge, fill=tuple(max(0, c - 60) for c in acc), anchor='mm')

    clean_title = strip_emoji(section_title)
    words = clean_title.split()
    lines, cur = [], ""
    for word in words:
        test = cur + word + " "
        if draw.textlength(test, font=font_title) > W - 70 and cur:
            lines.append(cur.strip())
            cur = word + " "
        else:
            cur = test
    if cur.strip():
        lines.append(cur.strip())

    ty = 85
    for line in lines[:3]:
        draw.text((28, ty), line, font=font_title, fill='white')
        bbox = draw.textbbox((28, ty), line, font=font_title)
        ty = bbox[3] + 10

    sub_color = tuple(min(255, c + 70) for c in acc)
    draw.text((28, H - 44), strip_emoji(base_topic)[:50], font=font_sub, fill=sub_color)

    buf = BytesIO()
    img.save(buf, format='JPEG', quality=92)
    buf.seek(0)
    return buf.getvalue()


def build_copy_text(data):
    lines = [f"# {data['title']}", "", data['intro'], ""]
    for s in data.get('sections', []):
        lines += [f"## {s['title']}", "", s['content'], ""]
    lines += ["## ✅ 마무리", data['conclusion'], ""]
    if data.get('faq'):
        lines += ["## ❓ 자주 묻는 질문 (FAQ)", ""]
        for item in data['faq']:
            lines += [f"**Q. {item.get('q', '')}**", f"A. {item.get('a', '')}", ""]
    hashtags = "  ".join(f"#{t.strip()}" for t in data.get('tags', '').split(',') if t.strip())
    lines += ["---", hashtags, ""]
    lines += ["👉 관련 제품은 아래에서 직접 비교해보세요: [여기에 쿠팡 파트너스 링크를 붙여넣으세요]", ""]
    lines += ["※ 이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받을 수 있습니다."]
    return "\n".join(lines)


def _get_secret(key):
    """Streamlit Cloud의 Secrets에 키가 설정돼 있으면 가져오고, 없으면 빈 문자열 (로컬 실행 시에도 에러 없이 동작)"""
    try:
        return st.secrets.get(key, "")
    except Exception:
        return ""


# 배포 시 Secrets에 키를 등록해두면 앱 열 때마다 다시 입력할 필요 없음 (특히 폰에서 유용)
if 'api_key' not in st.session_state and _get_secret("gemini_api_key"):
    st.session_state['api_key'] = _get_secret("gemini_api_key")
if 'pexels_key' not in st.session_state and _get_secret("pexels_api_key"):
    st.session_state['pexels_key'] = _get_secret("pexels_api_key")


# ── 메인 UI ─────────────────────────────────────────────────────

st.markdown("## ✍️ 티스토리 가전리뷰 생성기")
st.caption("AI 구매가이드 자동 생성 · 쿠팡파트너스 최적화")

with st.expander("🔑 Gemini API 키 설정", expanded='api_key' not in st.session_state):
    if st.session_state.get('api_key'):
        st.caption("✅ 저장된 키를 사용 중이에요. 바꾸려면 아래에 새로 입력하세요.")
    api_input = st.text_input("API 키", type="password",
                               value=st.session_state.get('api_key', ''),
                               placeholder="AIza... (aistudio.google.com 무료 발급)",
                               key="api_input_field")
    if st.button("저장", key="save_api"):
        if api_input.strip():
            st.session_state['api_key'] = api_input.strip()
            st.success("저장됐습니다!")
        else:
            st.warning("API 키를 입력해주세요.")

with st.expander("🖼 Pexels API 키 설정 (섹션 사진용, 선택)", expanded='pexels_key' not in st.session_state):
    if st.session_state.get('pexels_key'):
        st.caption("✅ 저장된 키를 사용 중이에요. 바꾸려면 아래에 새로 입력하세요.")
    pexels_input = st.text_input("Pexels API 키", type="password",
                                  value=st.session_state.get('pexels_key', ''),
                                  placeholder="pexels.com/api 무료 발급 (없으면 자동 배너로 대체)",
                                  key="pexels_input_field")
    if st.button("저장", key="save_pexels"):
        if pexels_input.strip():
            st.session_state['pexels_key'] = pexels_input.strip()
            st.success("저장됐습니다!")
        else:
            st.warning("API 키를 입력해주세요.")

st.divider()

col_cat, col_topic = st.columns([1, 2])
with col_cat:
    category = st.radio(
        "📂 카테고리",
        ["🧹 생활가전", "🍳 주방가전", "💻 모바일IT", "🖱 사무게이밍", "🌀 계절가전"],
        key="category"
    )
    cat = category.split(" ", 1)[1]

with col_topic:
    topic_input = st.text_input(
        "✏️ 주제 (비워두면 자동 선택)",
        placeholder="예) 무선청소기 고르는 법, 1인 가구 주방가전, 재택근무 모니터 추천 기준 ...",
        key="topic_input"
    )

gen_btn = st.button("✨  글 생성하기", type="primary", use_container_width=True)

if gen_btn:
    api_key = st.session_state.get('api_key', '').strip()
    if not api_key:
        st.error("API 키를 먼저 입력하고 저장해주세요.")
        st.stop()

    with st.status("글을 생성하고 있습니다...", expanded=True) as status:
        topic = topic_input.strip()
        if not topic:
            st.write("🔍 최신 트렌드에서 주제 자동 선택 중...")
            topic = get_trending_topic(cat)

        st.write(f"📰 '{topic}' 관련 뉴스 수집 중...")
        articles = get_news_context(topic, cat)

        st.write("📝 팩트 기반 구매가이드 작성 중...")
        try:
            data = generate_content(cat, topic, api_key, articles)
        except Exception as e:
            st.error(f"글 생성 오류: {e}")
            st.stop()

        sections = data.get('sections', [])
        st.write("🖼 관련 사진 검색 중...")
        pexels_key = st.session_state.get('pexels_key', '').strip()
        image_query = data.get('image_query_en', '').strip() or topic
        images_data = fetch_stock_photos(pexels_key, image_query, len(sections))

        if len(images_data) < len(sections):
            for i in range(len(images_data), len(sections)):
                st.write(f"🖼 대체 배너 생성 중 ({i+1}/{len(sections)})...")
                img_bytes = generate_image(cat, sections[i]['title'], topic, i)
                images_data.append(img_bytes)

        st.session_state['result_data'] = data
        st.session_state['result_images'] = images_data
        st.session_state['result_topic'] = topic
        status.update(label="✅ 완료!", state="complete")

if 'result_data' in st.session_state:
    data   = st.session_state['result_data']
    images = st.session_state['result_images']
    topic  = st.session_state['result_topic']

    st.divider()
    st.markdown(f"# {data.get('title', '')}")
    st.write(data.get('intro', ''))

    for i, section in enumerate(data.get('sections', [])):
        st.markdown(f"### {section.get('title', '')}")
        if i < len(images) and images[i]:
            st.image(images[i], use_container_width=True)
        st.write(section.get('content', ''))

    st.markdown("### ✅ 마무리")
    st.write(data.get('conclusion', ''))

    faq = data.get('faq', [])
    if faq:
        st.markdown("### ❓ 자주 묻는 질문 (FAQ)")
        for item in faq:
            with st.expander(f"Q. {item.get('q', '')}"):
                st.write(f"A. {item.get('a', '')}")

    raw_tags = data.get('tags', '')
    hashtags = "  ".join(f"#{t.strip()}" for t in raw_tags.split(',') if t.strip())
    st.caption(f"🏷 {hashtags}")

    st.info("👉 관련 제품은 실제 쿠팡 파트너스 링크로 직접 교체해서 넣어주세요. (아래 복사 텍스트에도 삽입 위치가 표시돼요)")

    st.divider()
    st.markdown("#### 📋 텍스트 복사 (마크다운 형식)")
    full_text = build_copy_text(data)
    st.text_area("아래 텍스트를 전체 선택해서 복사한 뒤, 티스토리 마크다운 에디터에 붙여넣으세요", value=full_text, height=320, key="copy_area")

    st.markdown("#### 💾 이미지 다운로드")
    img_cols = st.columns(len(images))
    for i, (col, img_bytes) in enumerate(zip(img_cols, images)):
        with col:
            col.download_button(
                label=f"이미지 {i+1}",
                data=img_bytes,
                file_name=f"blog_image_{i+1}.jpg",
                mime="image/jpeg",
                use_container_width=True,
            )
