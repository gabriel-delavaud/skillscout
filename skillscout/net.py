"""Accès HTTP, bibliothèque standard. Seul module du paquet qui appelle
`urlopen` : les tests n'ont qu'une cible à remplacer (`skillscout.net.urlopen`)."""
from __future__ import annotations

import contextlib
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HTTP_TIMEOUT = 20
USER_AGENT = "skillscout"


def get_json(url: str) -> dict:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode())


def get_text(url: str, max_chars: int) -> str:
    """Au plus `max_chars` caractères du document."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=HTTP_TIMEOUT) as r:
        # UTF-8 code un caractère sur 4 octets au plus : lire 4 × max_chars
        # octets donne `max_chars` caractères dès que le fichier les contient,
        # sans télécharger un fichier énorme en entier.
        return r.read(4 * max_chars).decode("utf-8", "replace")[:max_chars]


def post_json(url: str, body: dict, headers: dict, timeout: float) -> tuple[int, object]:
    """POST d'un corps JSON. Renvoie (statut HTTP, JSON décodé ou None s'il
    est illisible). Lève OSError si le serveur est injoignable ou lent."""
    req = Request(url, data=json.dumps(body).encode(), method="POST",
                  headers={**headers, "Content-Type": "application/json",
                           "User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except HTTPError as e:           # sous-classe d'OSError : à attraper d'abord
        with contextlib.closing(e):  # évite un ResourceWarning différé
            return e.code, None
    try:
        return status, json.loads(raw.decode())
    except ValueError:
        return status, None
