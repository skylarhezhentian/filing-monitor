"""Streamlit app for comparing recent SEC filings by ticker.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import difflib
import html
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SEC_BASE_URL = "https://www.sec.gov"
SEC_DATA_URL = "https://data.sec.gov"
USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "filing-monitor demo app contact@example.com",
)

INVESTOR_KEYWORDS = [
    "material weakness",
    "going concern",
    "liquidity",
    "debt",
    "covenant",
    "default",
    "impairment",
    "restructuring",
    "customer concentration",
    "subpoena",
    "investigation",
    "termination",
    "backlog",
    "gross margin",
    "cash flows",
]

FORM_TABS = [
    ("10-Q", "10-Q Comparison"),
    ("10-K", "10-K Comparison"),
    ("8-K", "8-K Comparison"),
]


@dataclass(frozen=True)
class Filing:
    """Small container for the SEC filing metadata this app needs."""

    form: str
    filing_date: str
    report_date: str
    accession_number: str
    primary_document: str
    cik: str

    @property
    def accession_without_dashes(self) -> str:
        return self.accession_number.replace("-", "")

    @property
    def document_url(self) -> str:
        return (
            f"{SEC_BASE_URL}/Archives/edgar/data/{int(self.cik)}/"
            f"{self.accession_without_dashes}/{self.primary_document}"
        )

    @property
    def sec_link(self) -> str:
        return (
            f"{SEC_BASE_URL}/ixviewer/doc/action?doc=/Archives/edgar/data/"
            f"{int(self.cik)}/{self.accession_without_dashes}/{self.primary_document}"
        )


def sec_headers() -> dict[str, str]:
    """Return headers SEC asks automated tools to send."""

    return {"User-Agent": USER_AGENT}


@lru_cache(maxsize=64)
def fetch_json(url: str) -> dict[str, Any]:
    """Fetch JSON from SEC endpoints and cache it for this process."""

    request = Request(url, headers=sec_headers())
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


@lru_cache(maxsize=64)
def fetch_text(url: str) -> str:
    """Fetch a filing document and cache it for this process."""

    request = Request(url, headers=sec_headers())
    with urlopen(request, timeout=60) as response:
        raw_document = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return raw_document.decode(charset, errors="replace")


@lru_cache(maxsize=1)
def load_ticker_lookup() -> dict[str, str]:
    """Load SEC's ticker-to-CIK mapping."""

    companies = fetch_json(f"{SEC_BASE_URL}/files/company_tickers.json")
    return {
        company["ticker"].upper(): str(company["cik_str"]).zfill(10)
        for company in companies.values()
    }


def get_cik_for_ticker(ticker: str) -> str | None:
    """Convert a ticker symbol to a zero-padded CIK string."""

    return load_ticker_lookup().get(ticker.strip().upper())


@lru_cache(maxsize=64)
def load_submissions(cik: str) -> dict[str, Any]:
    """Load a company's recent SEC submissions."""

    return fetch_json(f"{SEC_DATA_URL}/submissions/CIK{cik}.json")


def get_recent_filings(
    cik: str, submissions: dict[str, Any], form_type: str
) -> list[Filing]:
    """Return recent filings matching one form type from the SEC submissions JSON."""

    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])

    filings: list[Filing] = []
    for index, form in enumerate(forms):
        if form != form_type:
            continue
        filings.append(
            Filing(
                form=form,
                filing_date=filing_dates[index] if index < len(filing_dates) else "",
                report_date=report_dates[index] if index < len(report_dates) else "",
                accession_number=accessions[index] if index < len(accessions) else "",
                primary_document=(
                    primary_documents[index] if index < len(primary_documents) else ""
                ),
                cik=cik,
            )
        )
    return filings


class ReadableTextExtractor(HTMLParser):
    """Tiny HTML-to-text parser that keeps filing text readable."""

    BLOCK_TAGS = {
        "address",
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }
    SKIP_TAGS = {"script", "style", "ix:header", "header", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skipped_tag_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.SKIP_TAGS:
            self.skipped_tag_depth += 1
        elif tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.SKIP_TAGS and self.skipped_tag_depth:
            self.skipped_tag_depth -= 1
        elif tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skipped_tag_depth:
            self.parts.append(data)

    def text(self) -> str:
        text = html.unescape("".join(self.parts))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def extract_readable_text(document_html: str) -> str:
    """Convert a filing HTML document into readable plain text."""

    extractor = ReadableTextExtractor()
    extractor.feed(document_html)
    return extractor.text()


def split_into_paragraphs(text: str) -> list[str]:
    """Split filing text into paragraphs that are long enough to compare."""

    raw_paragraphs = re.split(r"\n{2,}", text)
    paragraphs = []
    for paragraph in raw_paragraphs:
        clean_paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if len(clean_paragraph) >= 140 and len(clean_paragraph.split()) >= 20:
            paragraphs.append(clean_paragraph)
    return paragraphs


def keyword_matches(paragraph: str) -> list[str]:
    """Return investor-relevant keywords found in a paragraph."""

    lower_paragraph = paragraph.lower()
    return [keyword for keyword in INVESTOR_KEYWORDS if keyword in lower_paragraph]


def highlight_keywords(paragraph: str) -> str:
    """Return paragraph HTML with investor keywords highlighted."""

    highlighted = html.escape(paragraph)
    for keyword in sorted(INVESTOR_KEYWORDS, key=len, reverse=True):
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        highlighted = pattern.sub(
            lambda match: f"<mark>{match.group(0)}</mark>", highlighted
        )
    return highlighted


def compare_paragraphs(latest_text: str, previous_text: str) -> list[dict[str, Any]]:
    """Find paragraphs that look new or materially changed in the latest filing."""

    latest_paragraphs = split_into_paragraphs(latest_text)
    previous_paragraphs = split_into_paragraphs(previous_text)

    changed_paragraphs: list[dict[str, Any]] = []
    for paragraph in latest_paragraphs:
        best_match_ratio = 0.0
        for previous_paragraph in previous_paragraphs:
            ratio = difflib.SequenceMatcher(
                None,
                paragraph[:3000],
                previous_paragraph[:3000],
                autojunk=False,
            ).ratio()
            best_match_ratio = max(best_match_ratio, ratio)
            if best_match_ratio >= 0.92:
                break

        matches = keyword_matches(paragraph)
        is_new_or_changed = best_match_ratio < 0.82
        has_meaningful_keyword_change = matches and best_match_ratio < 0.92
        if is_new_or_changed or has_meaningful_keyword_change:
            changed_paragraphs.append(
                {
                    "paragraph": paragraph,
                    "similarity": best_match_ratio,
                    "keywords": matches,
                    "score": len(matches) * 3 + (1 - best_match_ratio),
                }
            )

    changed_paragraphs.sort(key=lambda item: item["score"], reverse=True)
    return changed_paragraphs[:20]


def display_filing_metadata(label: str, filing: Filing) -> None:
    """Render filing metadata in a compact, readable block."""

    st.markdown(f"**{label}**")
    st.write(f"Filing date: {filing.filing_date or 'Not reported'}")
    st.write(f"Report date: {filing.report_date or 'Not reported'}")
    st.link_button("Open SEC filing", filing.sec_link)


def display_filing_comparison(
    cik: str,
    submissions: dict[str, Any],
    form_type: str,
) -> None:
    """Display the latest-vs-previous comparison for one SEC filing type."""

    filings = get_recent_filings(cik, submissions, form_type)
    if len(filings) < 2:
        st.info(f"Could not find two recent {form_type} filings for this company.")
        return

    latest_filing, previous_filing = filings[:2]

    latest_column, previous_column = st.columns(2)
    with latest_column:
        display_filing_metadata("Latest filing", latest_filing)
    with previous_column:
        display_filing_metadata("Previous filing", previous_filing)

    with st.spinner(f"Downloading and comparing {form_type} filings..."):
        latest_document = fetch_text(latest_filing.document_url)
        previous_document = fetch_text(previous_filing.document_url)
        latest_text = extract_readable_text(latest_document)
        previous_text = extract_readable_text(previous_document)
        changed_paragraphs = compare_paragraphs(latest_text, previous_text)

    st.subheader("Possible new or materially changed paragraphs")
    if not changed_paragraphs:
        st.success(
            f"No likely new or materially changed {form_type} paragraphs were found."
        )
        return

    st.caption(
        "Paragraphs with investor-relevant keywords are ranked higher. "
        "This is a screening aid, not a substitute for reading the filing."
    )
    for rank, item in enumerate(changed_paragraphs, start=1):
        keywords = ", ".join(item["keywords"]) if item["keywords"] else "None"
        change_level = round((1 - item["similarity"]) * 100)
        with st.expander(
            f"#{rank} | Estimated change: {change_level}% | Keywords: {keywords}",
            expanded=rank <= 3,
        ):
            st.markdown(highlight_keywords(item["paragraph"]), unsafe_allow_html=True)


def main() -> None:
    """Render the Streamlit application."""

    global st
    import streamlit as st

    st.set_page_config(page_title="SEC Filing Monitor", layout="wide")
    st.title("SEC Filing Monitor")
    st.write(
        "Enter a ticker to compare the latest two 10-Q, 10-K, and 8-K filings "
        "from SEC EDGAR."
    )

    ticker = st.text_input("Ticker", placeholder="Example: AAPL").strip().upper()
    if not ticker:
        st.info("Enter a ticker to begin.")
        return

    try:
        cik = get_cik_for_ticker(ticker)
        if not cik:
            st.error(f"Could not find a CIK for ticker {ticker}.")
            return

        submissions = load_submissions(cik)
        company_name = submissions.get("name", ticker)
        st.success(f"Loaded SEC submissions for {company_name} ({ticker}), CIK {cik}.")

        tabs = st.tabs([tab_label for _, tab_label in FORM_TABS])
        for tab, (form_type, _) in zip(tabs, FORM_TABS, strict=True):
            with tab:
                display_filing_comparison(cik, submissions, form_type)
    except HTTPError as error:
        st.error(f"SEC request failed: {error}")
    except URLError as error:
        st.error(f"Network request failed: {error}")


if __name__ == "__main__":
    main()
