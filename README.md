# Agentic E-Commerce Intelligence

A Flask app that aggregates recent agentic AI and e-commerce news from RSS feeds, lets you filter and select relevant articles, and generates a McKinsey-style executive briefing email from the selected items.

## Features

- Fetches recent articles from global and European retail and technology sources
- Filters articles using agentic AI and e-commerce keywords
- Supports search and region filtering in the UI
- Lets you select multiple articles and generate a structured briefing email

## Tech Stack

- Python
- Flask
- feedparser
- Vanilla HTML, CSS, and JavaScript

## Project Structure

```text
.
├── app.py
├── requirements.txt
└── templates/
    └── index.html
```

## Local Setup

1. Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the app:

```bash
python3 app.py
```

4. Open the app:

```text
http://127.0.0.1:5050
```

## Notes

- The app depends on external RSS feeds, so internet access is required for news fetching.
- Results depend on the availability and formatting of the configured feeds.

## Deployment

The repository includes a [render.yaml](/Users/joannazagorowski/Documents/Playground/render.yaml) file for easy deployment on Render-compatible hosting.
