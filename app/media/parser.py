from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Callable
from urllib.parse import urljoin, urlparse, urlunparse, unquote

from bs4 import BeautifulSoup
from colorama import Fore, Style, init

init(autoreset=True)


@dataclass
class ParsedDirEntry:
    name: str
    url: str
    modified: Optional[str]


@dataclass
class ParsedFileEntry:
    name: str
    url: str
    modified: Optional[str]
    size: Optional[str]


@dataclass
class ParsedPage:
    dir_modified: Optional[str]
    subdirs: List[ParsedDirEntry]
    files: List[ParsedFileEntry]


def _normalize_url(base: str, href: str) -> str:
    url = urljoin(base, href)
    parsed = urlparse(url)
    parsed = parsed._replace(fragment="")
    return urlunparse(parsed)


def _decode_name(text: str) -> str:
    return unquote(text.strip())


# ---------- Parsers for Different Directory Styles ----------


def _process_table_rows(
    rows,
    base_url: str,
    col_name: int,
    col_mod: Optional[int],
    col_size: Optional[int],
    is_dir_fn: Callable[[str, str, Optional[str]], bool],
    img_col: Optional[int] = None,
    ignore_labels=(),
    ignore_hrefs=()
) -> ParsedPage:
    subdirs: List[ParsedDirEntry] = []
    files: List[ParsedFileEntry] = []
    times: List[str] = []

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) <= col_name:
            continue

        name_td = tds[col_name]
        a = name_td.find("a")
        if not a:
            continue

        href = a.get("href", "")
        label = a.text.strip()

        alt = ""
        if img_col is not None and len(tds) > img_col:
            img = tds[img_col].find("img")
            alt = img.get("alt", "") if img else ""

        if href in ignore_hrefs or any(ign in label for ign in ignore_labels) or alt.upper().startswith("[PARENTDIR]"):
            continue

        modified = tds[col_mod].get_text(strip=True) if col_mod is not None and len(tds) > col_mod else None
        size = tds[col_size].get_text(strip=True) if col_size is not None and len(tds) > col_size else None

        if modified:
            times.append(modified)

        url = _normalize_url(base_url, href)
        decoded_name = _decode_name(label)

        if is_dir_fn(href, alt, size):
            subdirs.append(ParsedDirEntry(decoded_name, url, modified))
        else:
            files.append(ParsedFileEntry(decoded_name, url, modified, size))

    dir_modified = max(times) if times else None
    return ParsedPage(dir_modified, subdirs, files)


def _parse_generic_table(
    soup: BeautifulSoup, base_url: str, verbose: bool = True
) -> ParsedPage:
    if verbose:
        print(Fore.CYAN + f"[PARSER] Using generic directory parser for {base_url}")
    table = soup.find("table")
    if not table:
        if verbose:
            print(Fore.YELLOW + "  [WARN] No <table> found in generic parser.")
        return ParsedPage(None, [], [])

    page = _process_table_rows(
        rows=table.find_all("tr"),
        base_url=base_url,
        col_name=1,
        col_mod=2,
        col_size=3,
        img_col=0,
        ignore_hrefs=("../", ".."),
        is_dir_fn=lambda href, alt, size: href.endswith("/") or "[DIR]" in alt.upper() or "folder" in alt.lower()
    )
    if verbose:
        print(Fore.GREEN + f"  [OK] Parsed {len(page.subdirs)} subdirs, {len(page.files)} files (generic).")
    return page


def _parse_h5ai_fallback(
    soup: BeautifulSoup, base_url: str, verbose: bool = True
) -> ParsedPage:
    if verbose:
        print(Fore.CYAN + f"[PARSER] Using h5ai fallback parser for {base_url}")
    fb = soup.find("div", id="fallback")
    table = fb.find("table") if fb else None
    if not table:
        if verbose:
            print(Fore.YELLOW + "  [WARN] No table inside fallback <div>.")
        return ParsedPage(None, [], [])

    page = _process_table_rows(
        rows=table.find_all("tr"),
        base_url=base_url,
        col_name=1,
        col_mod=2,
        col_size=3,
        img_col=0,
        ignore_hrefs=("..",),
        ignore_labels=("Parent Directory",),
        is_dir_fn=lambda href, alt, size: href.endswith("/") or "folder" in alt.lower()
    )
    if verbose:
        print(Fore.GREEN + f"  [OK] Parsed {len(page.subdirs)} subdirs, {len(page.files)} files (h5ai fallback).")
    return page


def _parse_discovery_datatable(
    soup: BeautifulSoup, base_url: str, verbose: bool = True
) -> ParsedPage:
    if verbose:
        print(Fore.CYAN + f"[PARSER] Using discovery datatable parser for {base_url}")
    table = soup.find("table", id="example")
    if not table:
        if verbose:
            print(Fore.YELLOW + "  [WARN] No <table id='example'> found.")
        return ParsedPage(None, [], [])

    tbody = table.find("tbody") or table
    page = _process_table_rows(
        rows=tbody.find_all("tr"),
        base_url=base_url,
        col_name=1,
        col_mod=4,
        col_size=3,
        ignore_hrefs=("..", "../"),
        ignore_labels=("Parent Directory",),
        is_dir_fn=lambda href, alt, size: href.endswith("/") and (size is None or size == "")
    )
    if verbose:
        print(Fore.GREEN + f"  [OK] Parsed {len(page.subdirs)} subdirs, {len(page.files)} files (datatable).")
    return page


# ---------- Dispatcher ----------


def parse_directory_page(html: str, base_url: str, verbose: bool = True) -> ParsedPage:
    """
    Detect which style of listing this is and parse accordingly.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Order matters: test the most specific patterns first
    if soup.find("div", id="fallback"):
        return _parse_h5ai_fallback(soup, base_url, verbose=verbose)

    if soup.find("table", id="example"):
        return _parse_discovery_datatable(soup, base_url, verbose=verbose)

    return _parse_generic_table(soup, base_url, verbose=verbose)
