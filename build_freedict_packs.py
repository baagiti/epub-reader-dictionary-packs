#!/usr/bin/env python3
"""Build verified EPUB Workspace dictionary packs from pinned FreeDict releases."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import struct
import tarfile
import tempfile
import unicodedata
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path


API_URL = "https://freedict.org/freedict-database.json"
REPOSITORY = "baagiti/epub-reader-dictionary-packs"
RELEASE_TAG = "dictionary-packs-v1"
APPLICATION_ID = 0x45504449
SCHEMA_VERSION = 1
BUILD_TIME = "2026-08-13T00:00:00.000Z"
MAX_API_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_FILES = 20_000
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
NAMESPACE = uuid.UUID("5fcad1b0-5028-4cce-92e8-e46ecbd47128")

LANGUAGES = {
    "deu": ("de", "German"),
    "eng": ("en", "English"),
    "fra": ("fr", "French"),
    "spa": ("es", "Spanish"),
    "tur": ("tr", "Turkish"),
}


@dataclass(frozen=True)
class Spec:
    code: str
    version: str
    headwords: int
    stardict_sha512: str
    source_sha512: str


SPECS = (
    Spec("eng-deu", "1.9-fd1", 460315,
         "2dda62c64e96a0b1e04030ad028a7c1ae0b0ba71dde4095659657d87e4ff801e1734a70b239dba3e53428ec73609903b50ea0352fbdf17e5568105d74420ca5b",
         "e44d3a3697d2bfe93cadef70284355bdd9d41cae818722d26e9eda8e4268ce63c37cee4d623c4be8028467fc212d49b388fdb395c0a253d285aed1bd424f80a9"),
    Spec("eng-fra", "0.1.6", 8799,
         "abf56a59444b591deaa20f7eed8bdf2286b335f9469431bc4df744e2b3f34b1f64427356d43ed9751cf02eb9733a2058f83b05fbaa1c2a8d04c97502817c42a8",
         "7b7decb42f36617958303b8640616acc06e82aecd955e78949b67e083a7dbbdc4c668fee9cfcda20f219897767f64daabc942b3c3c01ed1f5e8d6ee56262e8a7"),
    Spec("eng-spa", "2025.11.23", 64258,
         "ddcb967c6e06a16287d7e0ab17fad38948b3a2bc7b0c73bb8778990efd54d9f9048cdf7d891d80810ab272929c66deb070559286c11d0cc65b208e1f84f07b6b",
         "622e8fec6c4178cb4c21e4577701c5325670f825331b07a185b4c6b810603c337e80429738a97581f68f668667910e7ad36f27332eaee2828f1f906537852fe3"),
    Spec("eng-tur", "0.3", 36589,
         "cb1142df3f4f1c2a0467af62567918361433c48bd42eb195e4b847adb4c6011a9efcd1ac1e2c57da42751654d54334a6ee2df3a001d147f20410fe131715b98e",
         "48f65bbe0be36c49498aa29e67d268532ad40ab1b4d7f2767f859eaa915491b33b62b498053052368fa839b77116c6b668f61879ee0113d1f409e6f4f45257a3"),
    Spec("deu-fra", "2025.11.23", 59631,
         "8202babaa973dab5989bb66c52c6b434476f7ebade7c844b085622b874a16931543c4be81863bc696b9d4fd11179d201646aab02624bfff1e2469184d45c3855",
         "ef87052072a0c2e48449264706b5c222444b9e05a7480394f67df5b964fd554eb2e60b391c89012b7a4d7d5cc437f750e4c3700254767907dcc02c0697b6f04c"),
    Spec("deu-spa", "2025.11.23", 36744,
         "6b718578be2650f01cdfd0c737cb48913340b303b838ba7a8ba656df32832e70e30871472fbc88e1b0a06bf6e7cda140772988a8753680ed2f6b799b1c1cf8b4",
         "37d5e623cf6c8ccb419d85b463953ec91577013a1737771895521f6d0fc5a5ed3e9253ae9b5e80faae1d8124d9b2c1efd65e65a3e3754289f56ea966df50b028"),
    Spec("deu-tur", "0.2.2", 36219,
         "efd8450e854f1636c032f11419347c0c80cf21f77470434905ec625348243185f9148156ebd2840360fa8d8cf8f04c062b160765415940504731c62c2f0e9d0a",
         "fd89fe69395190f4450dfda94033f20d7210c18d6dddd9b900e21ef0951ec05368c08d0f845cf1e231c17e2ebc8cafd827f72d44d14d7dcd152926ca0709ca07"),
    Spec("fra-deu", "2025.11.23", 48578,
         "b00287f4549a259faeec5218ed69f212e6976189870e95d03ebc45dd7b4849b6c3a8b32c9c5ee00d3bde30c666b1e252d4fa57b7dd6217ffd40f1f53b33de3ae",
         "cd1e85e9a0797411f769a14abc292ca438a790dd7ddc728630748ae536f5a957696201c955481e8c7943a3240fbf33a67e7f91d6b69561d3c28af9fca255ec55"),
    Spec("fra-spa", "2025.11.23", 52659,
         "aa8e4b5ed50fd359e672591ba9e6d64645fa02b34501f9c173684e77426fa71302a58a30224c3533a75ece06e52abcafd1a7eaab7241931aaada09d2cbef68f3",
         "7277527d01981f2e965e4fd7ee331297c6f8b9a1cf828f097b7288fb858e167fe8190c1c640855a4aa358d30d0525ee10ffe6b5beebb5059d152681522acf260"),
    Spec("fra-tur", "2025.11.23", 11628,
         "06301d0e46a73d1934bc4dadcd9c3a650194091e6e04fbdba8971a43276d5a865ab754a4eeb2e9a3a1d5925b521f0e58600dc62d8ed5113df266f570b3afd6fc",
         "7de06c4ec8e9f2a8312569b63bec3755dfea1ed132cdb914bbb72a690996a75ce0af0c78e1a85a6c4e6f3c5dd3cd97381da707c14771d8108d247b287de0634a"),
    Spec("spa-deu", "0.1", 21353,
         "0f16243b65517361863aedee5ac58b327bbb56f881742d92d0533c0af8fca329f279374f94e6f69c678c6880ee96d7e80e5a5051875ffb0285806505d3e8afbe",
         "8e81e22d4dcec46c0aa21aed20a9ed796e18647a31a6e7d5e9b172ff13c5b295fbf3bf04b8ebcd1cb47659c3a6c5909a9f011a81d1f2f01079732c53516d4a07"),
    Spec("spa-fra", "2025.11.23", 16273,
         "8b2b14b75c4e0b380daf5d7f13c052e541a69d894dc57dac4082571c450a0e0713edbaa4099cb06a1c79fd822d4f7221279d699032651b21904ea44dda4e176c",
         "e214ec097b084c9c6ce6c9d35ac8b37f661975641af1968d6813b909485c2f853e0d6f57bcb48b673993f3e09b009fa9d9f9cf82403b4fdbd8a3a0b88b5973d5"),
    Spec("spa-tur", "2024.10.10", 10068,
         "27b9daa94de4065fa0185f150a0732d718aebbb9c91426ce6c838c06d215f6172de4203927cc8151f547e8205be913d173797773bdfad107fb9698f4ec71711f",
         "08f54450ab5373955ec0f8f4aec45a6a22dd236befaa8e110d5e7d674ff586bdead6a1a4ccaec798a55f427e5c68503a41d045cbfc1b28ac5b8819670cb9fab2"),
    Spec("tur-eng", "0.3", 1026,
         "945f5a4a80f77a7f81392e6627c4e9e3bafdd8ab182862887385de0149cc0b9638eaea5c1c9e547dc7622dcb6db61a4cf59965a35b7b129f63002631bbe59795",
         "93009760bdab27ef958c637aecd72e4a2be83b7cc852f0931560bc5b74659adadb1012f8ea8f6ba0373816b42cc8658043d6bb9bcc192438d1b60feb99524e90"),
    Spec("tur-deu", "0.2.3", 941,
         "ec3ef149d738496d68b6c591c54a70c6dd351d3307da1881802b854dd95e686c863d4f698be1e803bafd9011358e87e915e6d176a7a34ada5f87cfcdd061c15c",
         "685be8407cd725a1c61a8fb1851c226132e45b7a4c5e4a2137d8b02428fc83ff68fad7837b3219db4d56b217f78887824dfeba2fc5328db1a5aa54086568c0c6"),
)


def fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "EPUB-Workspace-pack-builder/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(MAX_API_BYTES + 1)
    if len(data) > MAX_API_BYTES:
        raise ValueError("FreeDict API response exceeds the byte limit")
    return json.loads(data.decode("utf-8"))


def download(url: str, destination: Path, expected_size: int, expected_sha512: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "download.freedict.org":
        raise ValueError(f"Untrusted FreeDict download URL: {url}")
    digest = hashlib.sha512()
    written = 0
    request = urllib.request.Request(url, headers={"User-Agent": "EPUB-Workspace-pack-builder/1"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            written += len(chunk)
            if written > expected_size:
                raise ValueError(f"Download exceeds declared size: {url}")
            digest.update(chunk)
            output.write(chunk)
    if written != expected_size:
        raise ValueError(f"Download size mismatch for {url}: {written} != {expected_size}")
    if digest.hexdigest() != expected_sha512:
        raise ValueError(f"SHA-512 mismatch for {url}")


def safe_extract(archive: Path, destination: Path) -> None:
    total = 0
    with tarfile.open(archive, "r:xz") as source:
        members = source.getmembers()
        if len(members) > MAX_ARCHIVE_FILES:
            raise ValueError("Archive contains too many files")
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe archive path: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"Unsupported archive member: {member.name}")
            if member.isfile():
                total += member.size
                if total > MAX_EXPANDED_BYTES:
                    raise ValueError("Archive expands beyond the configured limit")
        source.extractall(destination, members=members, filter="data")


def detect_license(source_root: Path) -> tuple[str, str, str]:
    candidates: list[Path] = []
    for path in source_root.rglob("*"):
        if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            continue
        name = path.name.lower()
        if any(token in name for token in ("license", "licence", "copying")) or path.suffix.lower() in (".tei", ".xml"):
            candidates.append(path)
    evidence_parts: list[str] = []
    for path in candidates:
        try:
            evidence_parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    evidence = "\n".join(evidence_parts)
    folded = evidence.casefold()
    recognized = (
        ("creativecommons.org/licenses/by-sa/4.0", "CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
        ("creativecommons.org/licenses/by-sa/3.0", "CC BY-SA 3.0", "https://creativecommons.org/licenses/by-sa/3.0/"),
        ("gnu.org/licenses/gpl-3.0", "GPL-3.0-or-later", "https://www.gnu.org/licenses/gpl-3.0.html"),
        ("gnu.org/licenses/gpl-2.0", "GPL-2.0-or-later", "https://www.gnu.org/licenses/old-licenses/gpl-2.0.html"),
        ("gnu general public license", "GPL-2.0-or-later", "https://www.gnu.org/licenses/old-licenses/gpl-2.0.html"),
    )
    for marker, label, url in recognized:
        if marker in folded:
            notice = next((part for part in evidence_parts if marker in part.casefold()), evidence)
            return label, url, notice[:256 * 1024]
    raise ValueError("No approved commercial-use open license was found in the source archive")


def parse_ifo(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if not lines or lines[0] != "StarDict's dict ifo file":
        raise ValueError("Invalid StarDict IFO header")
    values: dict[str, str] = {}
    for line in lines[1:]:
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise ValueError("Invalid StarDict IFO field")
        values[key] = value
    if values.get("version") not in ("2.4.2", "3.0.0"):
        raise ValueError("Unsupported StarDict format")
    return values


def plain_text(source: str) -> str:
    source = re.sub(r"<\s*br\s*/?\s*>", "\n", source, flags=re.I)
    source = re.sub(r"</\s*(?:p|div|li|tr|h[1-6])\s*>", "\n", source, flags=re.I)
    source = re.sub(r"<[^>]*>", "", source)
    source = html.unescape(source)
    return "\n".join(line.strip() for line in source.splitlines() if line.strip())


def read_field(data: bytes, position: int, field_type: str, remainder: bool) -> tuple[bytes, int]:
    if remainder:
        return data[position:], len(data)
    if field_type.islower():
        end = data.find(b"\0", position)
        if end < 0:
            raise ValueError("StarDict text field lacks a terminator")
        return data[position:end], end + 1
    if position + 4 > len(data):
        raise ValueError("Truncated StarDict binary field")
    size = struct.unpack_from(">I", data, position)[0]
    start = position + 4
    end = start + size
    if end > len(data):
        raise ValueError("StarDict field exceeds its entry")
    return data[start:end], end


def parse_definition(data: bytes, sequence: str | None) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    position = 0
    types = list(sequence) if sequence else []
    if sequence:
        iterator = enumerate(types)
        for index, field_type in iterator:
            raw, position = read_field(data, position, field_type, index == len(types) - 1)
            text = raw.decode("utf-8")
            if field_type == "h":
                text = plain_text(text)
            if field_type.lower() not in set("mgtxykwhnr") or not text:
                raise ValueError(f"Unsupported or empty StarDict field: {field_type}")
            fields.append((field_type, text))
    else:
        while position < len(data):
            field_type = chr(data[position])
            position += 1
            raw, position = read_field(data, position, field_type, False)
            text = raw.decode("utf-8")
            if field_type == "h":
                text = plain_text(text)
            if field_type.lower() not in set("mgtxykwhnr") or not text:
                raise ValueError(f"Unsupported or empty StarDict field: {field_type}")
            fields.append((field_type, text))
    if position != len(data) or not fields:
        raise ValueError("Malformed StarDict entry")
    return fields


SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY NOT NULL CHECK(length(trim(key)) > 0), value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE entries (entry_id INTEGER PRIMARY KEY, headword TEXT NOT NULL CHECK(length(trim(headword)) > 0), normalized_headword TEXT NOT NULL CHECK(length(trim(normalized_headword)) > 0), part_of_speech TEXT CHECK(part_of_speech IS NULL OR length(trim(part_of_speech)) > 0));
CREATE TABLE definitions (definition_id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES entries(entry_id) ON DELETE CASCADE, definition_order INTEGER NOT NULL CHECK(definition_order >= 0), definition_text TEXT NOT NULL CHECK(length(definition_text) > 0), source_type TEXT CHECK(source_type IS NULL OR length(source_type) = 1), UNIQUE(entry_id, definition_order));
CREATE TABLE forms (form TEXT NOT NULL CHECK(length(trim(form)) > 0), normalized_form TEXT NOT NULL CHECK(length(trim(normalized_form)) > 0), entry_id INTEGER NOT NULL REFERENCES entries(entry_id) ON DELETE CASCADE, PRIMARY KEY(entry_id, normalized_form, form)) WITHOUT ROWID;
"""


def normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value.strip().lower())


def build_database(root: Path, output: Path, dictionary_id: str) -> tuple[str, int]:
    ifos = list(root.rglob("*.ifo"))
    if len(ifos) != 1:
        raise ValueError(f"Expected one IFO file, found {len(ifos)}")
    ifo_path = ifos[0]
    indexes = list(root.rglob("*.idx"))
    dictionaries = list(root.rglob("*.dict")) + list(root.rglob("*.dict.dz"))
    synonyms = list(root.rglob("*.syn"))
    if len(indexes) != 1 or len(dictionaries) != 1 or len(synonyms) > 1:
        raise ValueError(
            "Incomplete or ambiguous StarDict file set: "
            f"ifo={len(ifos)}, idx={len(indexes)}, dict={len(dictionaries)}, "
            f"syn={len(synonyms)}"
        )
    idx_path = indexes[0]
    dictionary_path = dictionaries[0]
    syn_path = synonyms[0] if synonyms else None
    values = parse_ifo(ifo_path)
    word_count = int(values["wordcount"])
    offset_bits = int(values.get("idxoffsetbits", "32"))
    sequence = values.get("sametypesequence")
    data = (
        gzip.decompress(dictionary_path.read_bytes())
        if dictionary_path.name.endswith(".dict.dz")
        else dictionary_path.read_bytes()
    )
    idx = idx_path.read_bytes()
    if len(idx) != int(values["idxfilesize"]):
        raise ValueError("StarDict index size mismatch")

    connection = sqlite3.connect(output)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.executescript(SCHEMA)
        for key, value in {
            "dictionary_id": dictionary_id,
            "source_format": "STARDICT",
            "stardict_version": values["version"],
            "book_name": values["bookname"],
            "word_count": values["wordcount"],
            **({"same_type_sequence": sequence} if sequence else {}),
        }.items():
            connection.execute("INSERT INTO metadata(key,value) VALUES (?,?)", (key, value))
        position = 0
        source_index = 0
        entry_count = 0
        definition_id = 0
        entry_ids: list[int | None] = []
        while position < len(idx):
            end = idx.find(b"\0", position)
            if end < 0:
                raise ValueError("Truncated StarDict index")
            headword = idx[position:end].decode("utf-8")
            position = end + 1
            width = 8 if offset_bits == 64 else 4
            if position + width + 4 > len(idx):
                raise ValueError("Truncated StarDict index record")
            offset = int.from_bytes(idx[position:position + width], "big")
            position += width
            size = int.from_bytes(idx[position:position + 4], "big")
            position += 4
            source_index += 1
            normalized = normalize(headword)
            if not normalized or size == 0:
                entry_ids.append(None)
                continue
            if offset + size > len(data):
                raise ValueError("StarDict entry points outside dictionary data")
            fields = parse_definition(data[offset:offset + size], sequence)
            entry_count += 1
            entry_ids.append(entry_count)
            connection.execute("INSERT INTO entries VALUES (?,?,?,NULL)", (entry_count, headword, normalized))
            for order, (field_type, definition) in enumerate(fields):
                definition_id += 1
                connection.execute("INSERT INTO definitions VALUES (?,?,?,?,?)", (definition_id, entry_count, order, definition, field_type))
        if source_index != word_count:
            raise ValueError(f"StarDict word count mismatch: {source_index} != {word_count}")
        if syn_path is not None:
            synonyms = syn_path.read_bytes()
            position = 0
            synonym_count = 0
            while position < len(synonyms):
                end = synonyms.find(b"\0", position)
                if end < 0 or end + 5 > len(synonyms):
                    raise ValueError("Truncated StarDict synonym record")
                form = synonyms[position:end].decode("utf-8")
                index = int.from_bytes(synonyms[end + 1:end + 5], "big")
                position = end + 5
                synonym_count += 1
                if index >= len(entry_ids):
                    raise ValueError("StarDict synonym points outside index")
                entry_id = entry_ids[index]
                normalized_form = normalize(form)
                if entry_id and normalized_form:
                    connection.execute("INSERT OR IGNORE INTO forms VALUES (?,?,?)", (form, normalized_form, entry_id))
            if synonym_count != int(values.get("synwordcount", "0")):
                raise ValueError("StarDict synonym count mismatch")
        connection.executescript("""
CREATE INDEX idx_entries_normalized_headword ON entries(normalized_headword, entry_id);
CREATE INDEX idx_forms_normalized_form ON forms(normalized_form, entry_id);
CREATE INDEX idx_definitions_entry_id ON definitions(entry_id, definition_order, definition_id);
CREATE INDEX idx_forms_entry_id ON forms(entry_id);
""")
        connection.commit()
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite quick_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite foreign_key_check failed")
        actual = connection.execute("SELECT count(*) FROM entries").fetchone()[0]
        if actual != entry_count:
            raise ValueError("SQLite entry count mismatch")
        connection.execute("PRAGMA optimize")
    finally:
        connection.close()
    return values["bookname"], entry_count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create_zip(source: Path, destination: Path) -> None:
    names = ("metadata.json", "dictionary.sqlite", "LICENSE.txt", "ATTRIBUTION.txt", "SOURCE.txt", "CHECKSUMS.sha256")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names:
            archive.write(source / name, arcname=name)


def build(spec: Spec, api_entry: dict, output: Path, scratch: Path) -> dict:
    source_code, target_code = spec.code.split("-")
    source_language, source_name = LANGUAGES[source_code]
    target_language, target_name = LANGUAGES[target_code]
    if api_entry.get("edition") != spec.version or int(api_entry.get("headwords", -1)) != spec.headwords:
        raise ValueError(f"Pinned metadata mismatch for {spec.code}")
    releases = {item["platform"]: item for item in api_entry["releases"]}
    star = releases["stardict"]
    src = releases["src"]
    if star["version"] != spec.version or src["version"] != spec.version:
        raise ValueError(f"Pinned release version mismatch for {spec.code}")
    if star["checksum"] != spec.stardict_sha512 or src["checksum"] != spec.source_sha512:
        raise ValueError(f"Pinned upstream checksum mismatch for {spec.code}")

    pair_root = scratch / spec.code
    pair_root.mkdir()
    star_archive = pair_root / Path(star["URL"]).name
    source_archive = pair_root / Path(src["URL"]).name
    download(star["URL"], star_archive, int(star["size"]), spec.stardict_sha512)
    download(src["URL"], source_archive, int(src["size"]), spec.source_sha512)
    star_root = pair_root / "stardict"
    source_root = pair_root / "source"
    star_root.mkdir()
    source_root.mkdir()
    safe_extract(star_archive, star_root)
    safe_extract(source_archive, source_root)
    license_label, license_url, license_notice = detect_license(source_root)

    dictionary_id = str(uuid.uuid5(NAMESPACE, f"freedict:{spec.code}:{spec.version}"))
    package_root = pair_root / "package"
    package_root.mkdir()
    database = package_root / "dictionary.sqlite"
    book_name, entry_count = build_database(star_root, database, dictionary_id)
    display_name = f"{source_name}–{target_name} FreeDict"
    metadata = {
        "dictionary_id": dictionary_id,
        "name": display_name,
        "source_language": source_language,
        "target_language": target_language,
        "version": spec.version,
        "format_version": SCHEMA_VERSION,
        "entry_count": entry_count,
        "installed_at": BUILD_TIME,
    }
    (package_root / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (package_root / "LICENSE.txt").write_text(
        f"Dictionary data license: {license_label}\nLicense: {license_url}\n\n"
        f"The exact FreeDict source archive and its notices are distributed beside this package.\n\n"
        f"Upstream notice excerpt:\n{license_notice}\n", encoding="utf-8")
    (package_root / "ATTRIBUTION.txt").write_text(
        f"{display_name}\nDictionary content: FreeDict contributors\n"
        f"Upstream maintainer: {api_entry.get('maintainerName', 'FreeDict')}\n"
        f"Upstream source: {api_entry.get('sourceURL', 'https://freedict.org/')}\n"
        "Application SQLite conversion: EPUB Workspace\n"
        "Changes: the FreeDict StarDict release was converted to the application's normalized SQLite schema; HTML fields were converted to plain text.\n",
        encoding="utf-8")
    (package_root / "SOURCE.txt").write_text(
        f"FreeDict dictionary: {spec.code}\nVersion: {spec.version}\n"
        f"StarDict archive: {star['URL']}\nStarDict SHA-512: {spec.stardict_sha512}\n"
        f"Source archive: {src['URL']}\nSource SHA-512: {spec.source_sha512}\n"
        f"FreeDict API: {API_URL}\n", encoding="utf-8")
    checksum_names = ("metadata.json", "dictionary.sqlite", "LICENSE.txt", "ATTRIBUTION.txt", "SOURCE.txt")
    (package_root / "CHECKSUMS.sha256").write_text(
        "".join(f"{sha256_file(package_root / name)}  {name}\n" for name in checksum_names), encoding="utf-8")

    slug = f"{source_name.lower()}-{target_name.lower()}-freedict"
    zip_path = output / f"{slug}.dictpack.zip"
    create_zip(package_root, zip_path)
    source_asset = output / source_archive.name
    shutil.copy2(source_archive, source_asset)
    installed_bytes = sum((package_root / name).stat().st_size for name in (*checksum_names, "CHECKSUMS.sha256"))
    print(f"{spec.code}: {entry_count} entries, {zip_path.stat().st_size} archive bytes")
    return {
        "dictionary_id": dictionary_id,
        "name": display_name,
        "source_language": source_language,
        "target_language": target_language,
        "version": spec.version,
        "format_version": SCHEMA_VERSION,
        "entry_count": entry_count,
        "archive_url": f"https://github.com/{REPOSITORY}/releases/download/{RELEASE_TAG}/{zip_path.name}",
        "archive_sha256": sha256_file(zip_path),
        "archive_bytes": zip_path.stat().st_size,
        "installed_bytes": installed_bytes,
        "license_label": license_label,
        "license_url": license_url,
        "attribution": f"FreeDict contributors; {book_name}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("release"))
    parser.add_argument("--base-catalog", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    api = fetch_json(API_URL)
    if not isinstance(api, list):
        raise ValueError("FreeDict API root must be an array")
    entries = {item["name"]: item for item in api if isinstance(item, dict) and "name" in item}
    packages: list[dict] = []
    if args.base_catalog:
        base = json.loads(args.base_catalog.read_text(encoding="utf-8"))
        packages.extend(base["packages"])
    with tempfile.TemporaryDirectory(prefix="freedict-build-") as temporary:
        scratch = Path(temporary)
        for spec in SPECS:
            packages.append(build(spec, entries[spec.code], args.output, scratch))
    seen: set[tuple[str, str, str]] = set()
    for package in packages:
        key = (package["source_language"], package.get("target_language"), package["dictionary_id"])
        if key in seen:
            raise ValueError(f"Duplicate catalog package: {key}")
        seen.add(key)
    catalog = {"format_version": 1, "generated_at": "2026-08-13T00:00:00.000Z", "packages": packages}
    (args.output / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
