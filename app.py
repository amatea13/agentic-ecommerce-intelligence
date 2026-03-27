from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
import re
import sqlite3

import feedparser
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)
DB_PATH = Path(app.root_path) / "news_cache.db"
HISTORY_DAYS = 30


RSS_FEEDS = [
    {"name": "TechCrunch", "url": "https://techcrunch.com/feed/", "region": "Global"},
    {"name": "VentureBeat", "url": "https://venturebeat.com/feed/", "region": "Global"},
    {"name": "Wired", "url": "https://www.wired.com/feed/rss", "region": "Global"},
    {"name": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "region": "Global"},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/feed/", "region": "Global"},
    {"name": "PYMNTS", "url": "https://www.pymnts.com/feed/", "region": "Global"},
    {"name": "Finextra", "url": "https://www.finextra.com/rss/headlines.aspx", "region": "Global"},
    {"name": "eCommerce News EU", "url": "https://ecommercenews.eu/feed/", "region": "Europe"},
    {"name": "Retail Gazette", "url": "https://www.retailgazette.co.uk/feed/", "region": "Europe"},
    {"name": "Internet Retailing", "url": "https://internetretailing.net/feed/", "region": "Europe"},
]


KEYWORDS = [
    "agentic",
    "ai agent",
    "llm",
    "large language model",
    "generative ai",
    "gen ai",
    "autonomous",
    "ecommerce",
    "e-commerce",
    "online retail",
    "digital commerce",
    "ai shopping",
    "conversational commerce",
    "chatbot",
    "copilot",
    "openai",
    "anthropic",
    "retail ai",
    "shopping agent",
    "checkout",
    "recommendation engine",
    "personalization",
    "omnichannel",
    "fulfillment ai",
    "inventory ai",
]


def is_relevant(title, summary):
    text = (title + " " + summary).lower()
    return any(keyword in text for keyword in KEYWORDS)


def get_db_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                cache_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                summary TEXT,
                link TEXT NOT NULL,
                source TEXT NOT NULL,
                region TEXT NOT NULL,
                date TEXT NOT NULL,
                sort_date TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_articles_sort_date
            ON articles(sort_date DESC)
            """
        )
        connection.commit()


def article_cache_key(article):
    link = (article.get("link") or "").strip().lower()
    if link:
        return link

    title = re.sub(r"\s+", " ", article.get("title", "").strip().lower())
    return f"{article.get('source', '').lower()}::{title}"


def persist_articles(articles):
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db_connection() as connection:
        for article in articles:
            cache_key = article_cache_key(article)
            connection.execute(
                """
                INSERT INTO articles (
                    cache_key, title, summary, link, source, region, date, sort_date, first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    title = excluded.title,
                    summary = excluded.summary,
                    link = excluded.link,
                    source = excluded.source,
                    region = excluded.region,
                    date = excluded.date,
                    sort_date = excluded.sort_date,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    cache_key,
                    article["title"],
                    article.get("summary", ""),
                    article.get("link", ""),
                    article["source"],
                    article["region"],
                    article["date"],
                    article["sort_date"],
                    now_iso,
                    now_iso,
                ),
            )

        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
        connection.execute("DELETE FROM articles WHERE sort_date < ?", (cutoff_iso,))
        connection.commit()


def load_cached_articles():
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT title, summary, link, source, region, date, sort_date
            FROM articles
            WHERE sort_date >= ?
            ORDER BY sort_date DESC
            """,
            (cutoff_iso,),
        ).fetchall()

    return [
        {
            "id": f"{row['source']}-{hash(row['link'])}",
            "title": row["title"],
            "summary": row["summary"],
            "link": row["link"],
            "source": row["source"],
            "region": row["region"],
            "date": row["date"],
            "sort_date": row["sort_date"],
        }
        for row in rows
    ]


def deduplicate_articles(articles):
    seen_titles = set()
    unique_articles = []
    for article in articles:
        key = re.sub(r"[^a-z0-9]", "", article["title"].lower())[:60]
        if key not in seen_titles:
            seen_titles.add(key)
            unique_articles.append(article)
    return unique_articles


def fetch_feed(feed_info):
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    try:
        feed = feedparser.parse(feed_info["url"])
        for entry in feed.entries[:30]:
            title = unescape(entry.get("title", "").strip())
            summary = unescape(re.sub(r"<[^>]+>", "", entry.get("summary", ""))).strip()
            link = entry.get("link", "")

            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                try:
                    pub_date = datetime(*published[:6], tzinfo=timezone.utc)
                    if pub_date < cutoff:
                        continue
                    date_str = pub_date.strftime("%b %d, %Y")
                    sort_date = pub_date.isoformat()
                except Exception:
                    date_str = "Recent"
                    sort_date = datetime.now(timezone.utc).isoformat()
            else:
                date_str = "Recent"
                sort_date = datetime.now(timezone.utc).isoformat()

            summary = re.sub(r"The post .+ appeared first on .+\.", "", summary).strip()
            summary = re.sub(r"\s{2,}", " ", summary)

            if is_relevant(title, summary):
                articles.append(
                    {
                        "id": f"{feed_info['name']}-{hash(link)}",
                        "title": title,
                        "summary": summary[:400],
                        "link": link,
                        "source": feed_info["name"],
                        "region": feed_info["region"],
                        "date": date_str,
                        "sort_date": sort_date,
                    }
                )
    except Exception as exc:
        print(f"[WARN] Error fetching {feed_info['name']}: {exc}")

    return articles


def fetch_all_news():
    fresh_articles = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(fetch_feed, feed): feed for feed in RSS_FEEDS}
        for future in as_completed(futures, timeout=15):
            try:
                fresh_articles.extend(future.result())
            except Exception as exc:
                print(f"[WARN] Feed fetch error: {exc}")

    if fresh_articles:
        persist_articles(fresh_articles)

    cached_articles = load_cached_articles()
    unique_articles = deduplicate_articles(cached_articles)
    unique_articles.sort(key=lambda article: article.get("sort_date", ""), reverse=True)
    return unique_articles


def categorize_articles(articles):
    categories = {
        "AI Agents & Automation": [],
        "E-Commerce Platforms & Technology": [],
        "Market Trends & Consumer Behaviour": [],
        "Industry & Regulatory Developments": [],
    }

    for article in articles:
        text = (article["title"] + " " + article.get("summary", "")).lower()
        if any(
            keyword in text
            for keyword in [
                "agent",
                "agentic",
                "autonomous",
                "llm",
                "generative ai",
                "gen ai",
                "openai",
                "anthropic",
                "copilot",
            ]
        ):
            categories["AI Agents & Automation"].append(article)
        elif any(
            keyword in text
            for keyword in [
                "platform",
                "technology",
                "software",
                "tool",
                "api",
                "integration",
                "checkout",
                "fulfillment",
                "inventory",
            ]
        ):
            categories["E-Commerce Platforms & Technology"].append(article)
        elif any(
            keyword in text
            for keyword in [
                "consumer",
                "trend",
                "behaviour",
                "behavior",
                "market",
                "growth",
                "retail",
                "shopping",
                "omnichannel",
                "personali",
            ]
        ):
            categories["Market Trends & Consumer Behaviour"].append(article)
        else:
            categories["Industry & Regulatory Developments"].append(article)

    return {name: items for name, items in categories.items() if items}


def get_article_insight(article):
    raw_summary = re.sub(r"<[^>]+>", "", article.get("summary", "")).strip()
    if not raw_summary:
        return "No summary available."

    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", raw_summary) if segment.strip()]
    if not sentences:
        return raw_summary[:180]

    first_sentence = sentences[0]
    return first_sentence if len(first_sentence) <= 220 else first_sentence[:217] + "..."


def build_theme_takeaway(category, items, region_name):
    category_messages = {
        "AI Agents & Automation": (
            f"{region_name} signals confirm that agentic capabilities are moving beyond experimentation "
            "and toward embedded commercial workflows."
        ),
        "E-Commerce Platforms & Technology": (
            f"{region_name} coverage highlights continued investment in the commerce stack, especially in "
            "the tools, integrations, and infrastructure needed to operationalise AI at scale."
        ),
        "Market Trends & Consumer Behaviour": (
            f"{region_name} news points to a changing demand environment in which customer expectations, "
            "shopping behaviour, and digital adoption continue to evolve."
        ),
        "Industry & Regulatory Developments": (
            f"{region_name} developments underline that policy, competitive moves, and structural industry "
            "changes remain important constraints on execution."
        ),
    }
    base_message = category_messages.get(
        category,
        f"{region_name} developments in this theme carry direct implications for commercial priorities."
    )
    return f"{base_message} {len(items)} relevant development{'s' if len(items) != 1 else ''} were identified."


def build_region_overview(region_name, articles, categories):
    if not articles:
        return ""

    lead_theme = max(categories.items(), key=lambda item: len(item[1]))[0]
    if region_name == "World":
        return (
            f"World news is led by {lead_theme.lower()}, indicating that global players are accelerating "
            "AI-enabled commerce capabilities and the surrounding platform ecosystem."
        )

    return (
        f"Europe coverage is led by {lead_theme.lower()}, with a stronger emphasis on retail operating "
        "performance, market development, and policy-related execution realities."
    )


def generate_mckinsey_email(articles):
    today = datetime.now().strftime("%B %d, %Y")
    categorized = categorize_articles(articles)
    total = len(articles)
    source_list = ", ".join(sorted(set(article["source"] for article in articles)))

    lines = []
    lines.append(f"Subject: Agentic E-Commerce Intelligence Briefing — {today}")
    lines.append("")
    lines.append("Dear [Name],")
    lines.append("")
    lines.append("EXECUTIVE SUMMARY")
    lines.append("─" * 60)

    if total == 1:
        lines.append(
            "One development of strategic relevance to our agentic e-commerce "
            "positioning has emerged this week. A focused response is recommended."
        )
    else:
        lines.append(
            f"{total} developments of strategic relevance to our agentic e-commerce "
            f"positioning have emerged this week, spanning "
            f"{len(categorized)} distinct theme{'s' if len(categorized) > 1 else ''}. "
            "Immediate awareness and a structured response are recommended."
        )

    lines.append("")
    lines.append("")
    lines.append("KEY DEVELOPMENTS")
    lines.append("─" * 60)

    section_num = 0
    for category, items in categorized.items():
        section_num += 1
        lines.append("")
        lines.append(f"{section_num}. {category.upper()}")
        lines.append("")
        for item_num, article in enumerate(items, 1):
            lines.append(f"   {section_num}.{item_num}  {article['title']}")
            lines.append(
                f"         Source: {article['source']} ({article['region']}) | {article['date']}"
            )
            if article.get("summary"):
                lines.append(f"         Insight: {get_article_insight(article)}")
            lines.append("")

    lines.append("")
    lines.append("STRATEGIC IMPLICATIONS")
    lines.append("─" * 60)
    lines.append("")
    lines.append(
        "The above developments point to three mutually exclusive, collectively "
        "exhaustive implications for our business:"
    )
    lines.append("")
    lines.append(
        "   1. Competitive Urgency — Agentic capabilities are moving from experimental "
        "to production at scale. Laggards risk structural disadvantage within 12–18 months."
    )
    lines.append("")
    lines.append(
        "   2. Operational Readiness — Capturing value from these trends requires "
        "proactive investment in AI-ready data infrastructure and cross-functional talent."
    )
    lines.append("")
    lines.append(
        "   3. Customer Expectation Reset — Consumers interacting with agentic commerce "
        "interfaces will rapidly elevate baseline expectations, redefining the competitive floor."
    )
    lines.append("")
    lines.append("")
    lines.append("RECOMMENDED NEXT STEPS")
    lines.append("─" * 60)
    lines.append("")
    lines.append(
        "   →  Benchmark our current agentic e-commerce maturity against the developments above"
    )
    lines.append("   →  Identify 1–2 high-ROI initiatives for immediate prioritisation")
    lines.append(
        "   →  Schedule a 30-minute alignment session to agree on our strategic response"
    )
    lines.append("")
    lines.append("─" * 60)
    lines.append("")
    lines.append("Best regards,")
    lines.append("[Your Name]")
    lines.append("")
    lines.append(f"Sources: {source_list}")
    lines.append(f"Briefing compiled: {today}")

    return "\n".join(lines)


def generate_executive_summary(articles):
    today = datetime.now().strftime("%B %d, %Y")
    regions = {"World": [], "Europe": []}

    for article in articles:
        region_name = "Europe" if article["region"] == "Europe" else "World"
        regions[region_name].append(article)

    lines = []
    lines.append(f"EXECUTIVE SUMMARY — AGENTIC E-COMMERCE | {today}")
    lines.append("")
    lines.append(
        "This briefing synthesizes the most relevant developments across World and Europe "
        "into a MECE structure for executive review and direct email use."
    )
    lines.append("")
    lines.append("AT A GLANCE")
    lines.append("─" * 60)
    lines.append(
        f"{len(articles)} relevant developments were identified across "
        f"{sum(1 for items in regions.values() if items)} geographies."
    )
    lines.append(
        "The headline message is that agentic AI is moving from capability exploration toward practical "
        "commerce deployment, while retailers continue to adapt their operating models, customer experiences, "
        "and enabling technology stacks."
    )

    section_num = 0
    for region_name in ["World", "Europe"]:
        region_articles = regions[region_name]
        if not region_articles:
            continue

        section_num += 1
        region_categories = categorize_articles(region_articles)
        lines.append("")
        lines.append(f"{section_num}. {region_name.upper()}")
        lines.append("─" * 60)
        lines.append(build_region_overview(region_name, region_articles, region_categories))

        theme_num = 0
        for category, items in region_categories.items():
            theme_num += 1
            lines.append("")
            lines.append(f"   {section_num}.{theme_num} {category}")
            lines.append(f"      Takeaway: {build_theme_takeaway(category, items, region_name)}")
            for article in items:
                lines.append(f"      - {article['title']}")
                lines.append(
                    f"        Why it matters: {get_article_insight(article)}"
                )
                lines.append(
                    f"        Source: {article['source']} | {article['date']} | {article['link']}"
                )

    implications_section = section_num + 1
    sources_section = implications_section + 1

    lines.append("")
    lines.append(f"{implications_section}. EXECUTIVE IMPLICATIONS")
    lines.append("─" * 60)
    lines.append(
        "1. Capability race: Competitive differentiation will increasingly depend on how quickly firms "
        "convert AI experimentation into reliable, revenue-linked commerce workflows."
    )
    lines.append(
        "2. Operating model pressure: Capturing value will require better data foundations, tighter integration "
        "layers, and stronger coordination across commercial, product, and technology teams."
    )
    lines.append(
        "3. Geographic nuance: Europe remains more shaped by market structure and execution constraints, while "
        "World developments continue to indicate the broader pace of platform and capability innovation."
    )
    lines.append(
        "4. Leadership agenda: Management teams should use the current wave of developments to sharpen "
        "prioritisation, focusing on a short list of use cases with clear strategic and operational payback."
    )

    lines.append("")
    lines.append(f"{sources_section}. SOURCE LINKS")
    lines.append("─" * 60)
    for region_name in ["World", "Europe"]:
        region_articles = [article for article in articles if (article["region"] == "Europe") == (region_name == "Europe")]
        if not region_articles:
            continue
        lines.append(f"{region_name}:")
        for article in region_articles:
            lines.append(
                f"- {article['title']} ({article['source']}, {article['date']}): {article['link']}"
            )
        lines.append("")

    return "\n".join(lines)


init_db()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/news")
def get_news():
    articles = fetch_all_news()
    return jsonify(
        {
            "articles": articles,
            "count": len(articles),
            "fetched_at": datetime.now().isoformat(),
        }
    )


@app.route("/api/generate-email", methods=["POST"])
def generate_email():
    data = request.get_json()
    selected = data.get("articles", [])
    if not selected:
        return jsonify({"error": "No articles selected"}), 400

    email = generate_mckinsey_email(selected)
    return jsonify({"email": email})


@app.route("/api/generate-summary", methods=["POST"])
def generate_summary():
    data = request.get_json()
    selected = data.get("articles", [])
    if not selected:
        return jsonify({"error": "No articles selected"}), 400

    summary = generate_executive_summary(selected)
    return jsonify({"summary": summary})


if __name__ == "__main__":
    app.run(debug=True, port=5050)
