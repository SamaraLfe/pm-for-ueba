"""Чтение сетевых данных MIT и присоединение разметки."""

import gzip
import hashlib
import socket
import struct
import csv
from collections import Counter, defaultdict
from pathlib import Path
from io_utils import partition, save_csv, save_json

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data/raw"
OUT = ROOT / "data/prepared"


def packets(path):
    """Последовательно прочитать каждый пакет classic PCAP, включая полный gzip-файл."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as source:
        header = source.read(24)
        if header[:4] not in (b"\xa1\xb2\xc3\xd4", b"\xd4\xc3\xb2\xa1"):
            raise ValueError("Not classic microsecond PCAP")
        endian = ">" if header[0] == 161 else "<"
        linktype = struct.unpack(endian + "I", header[20:24])[0]
        if linktype not in (1, 19):
            raise ValueError(f"Unexpected link type {linktype}")
        number = 0
        while record := source.read(16):
            if len(record) != 16:
                raise ValueError("Truncated packet header")
            sec, usec, captured, original = struct.unpack(endian + "IIII", record)
            frame = source.read(captured)
            if len(frame) != captured or captured > original:
                raise ValueError("Truncated packet")
            number += 1
            yield (number, sec * 1000000 + usec, frame)


def fingerprint(ts, frame):
    """Сопоставить пакет с разметкой по точному времени и сохранённым байтам."""
    return hashlib.sha256(struct.pack(">Q", ts) + frame).digest()


def decode(frame):
    """Извлечь IPv4-адреса, порты и протокол из Ethernet-кадра этих файлов MIT."""
    if len(frame) < 34 or frame[12:14] != b"\x08\x00":
        return (None, "non_ipv4_or_short_ethernet")
    ip = frame[14:]
    size = (ip[0] & 15) * 4
    if ip[0] >> 4 != 4 or size < 20 or len(ip) < size:
        return (None, "bad_ip")
    src, dst = (socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20]))
    if int.from_bytes(ip[6:8], "big") & 16383:
        return (None, "fragment")
    payload = ip[size:]
    proto = ip[9]
    if proto in (6, 17) and len(payload) >= (20 if proto == 6 else 8):
        sport, dport = struct.unpack(">HH", payload[:4])
        return ((src, dst, sport, dport, "TCP" if proto == 6 else "UDP"), None)
    if proto == 1 and len(payload) >= 8:
        ident = (
            int.from_bytes(payload[4:6], "big")
            if payload[0] in (0, 8)
            else 65536 + payload[0]
        )
        return ((src, dst, ident, ident, "ICMP"), None)
    return (None, "other_protocol_or_short_transport")


def service(proto, sport, dport):
    """Назначить фиксированную сервисную категорию без использования меток атаки."""
    ports = {sport, dport}
    if proto == "ICMP":
        return "ICMP"
    if proto == "UDP":
        return "UDP_DNS" if 53 in ports else "UDP_RPC" if 111 in ports else "UDP_OTHER"
    for name, known in [
        ("WEB", {80, 443}),
        ("MAIL", {25, 110, 143}),
        ("REMOTE", {22, 23, 513, 514}),
        ("FILE", {20, 21, 139, 445}),
        ("RPC", {111}),
    ]:
        if ports & known:
            return "TCP_" + name
    return "TCP_OTHER"


def internal(ip):
    """Проверить принадлежность к исследуемому внутреннему диапазону 172.16/16."""
    return ip.startswith("172.16.")


def read_attack_labels():
    """Построить индекс точных отпечатков пяти официальных файлов разметки."""
    labels = defaultdict(set)
    counts = Counter()
    for phase in range(1, 6):
        for _, timestamp, frame in packets(RAW / f"phase-{phase}-dump"):
            labels[fingerprint(timestamp, frame)].add(phase)
            counts[phase] += 1
    return labels, counts


def add_packet(active, events, timestamp, frame, decoded, phases):
    """Связать пакет с двунаправленным разговором и сохранить его статистики."""
    src, dst, sport, dport, protocol = decoded
    endpoints = sorted(((src, sport), (dst, dport)))
    key = (protocol, *endpoints)
    event = active.get(key)
    new_block = (
        event is None
        or timestamp - event["end_us"] > 60_000_000
        or timestamp - event["start_us"] >= 300_000_000
    )
    if new_block:
        event = {
            "event_id": f"f{len(events) + 1:07d}",
            "start_us": timestamp,
            "end_us": timestamp,
            "src": src,
            "dst": dst,
            "sport": sport,
            "dport": dport,
            "protocol": protocol,
            "service": service(protocol, sport, dport),
            "packets": 0,
            "bytes": 0,
            "attack_packets": 0,
            "phases": set(),
        }
        events.append(event)
        active[key] = event
    event["end_us"] = max(event["end_us"], timestamp)
    event["packets"] += 1
    event["bytes"] += len(frame)
    event["attack_packets"] += bool(phases)
    event["phases"].update(phases)
    return event["event_id"]


def main():
    """Прочитать полный capture; ни один исходный пакет не пропускается молча."""
    OUT.mkdir(parents=True, exist_ok=True)
    labels, label_counts = read_attack_labels()
    active = {}
    events = []
    excluded = Counter()
    matched = Counter()
    matched_fingerprints = set()
    minimum = maximum = first_attack = None
    with gzip.open(
        OUT / "packet_membership.csv.gz", "wt", encoding="utf-8", newline=""
    ) as output:
        writer = csv.writer(output)
        writer.writerow(
            ["packet_id", "time_us", "flow_id", "attack_phases", "decode_status"]
        )
        for number, timestamp, frame in packets(RAW / "inside_full.dump.gz"):
            minimum = timestamp if minimum is None else min(minimum, timestamp)
            maximum = timestamp if maximum is None else max(maximum, timestamp)
            packet_hash = fingerprint(timestamp, frame)
            phases = labels.get(packet_hash, set())
            if phases:
                matched.update(phases)
                matched_fingerprints.add(packet_hash)
                first_attack = (
                    timestamp if first_attack is None else min(first_attack, timestamp)
                )
            decoded, error = decode(frame)
            event_id = ""
            if error:
                excluded[error] += 1
            else:
                event_id = add_packet(active, events, timestamp, frame, decoded, phases)
            phase_text = ";".join(map(str, sorted(phases)))
            writer.writerow([number, timestamp, event_id, phase_text, error or "decoded"])
    if matched_fingerprints != set(labels):
        raise ValueError("Не все пакеты официальной разметки найдены в capture")
    if first_attack is None or first_attack - minimum < 60_000_000:
        raise ValueError("Недостаточный интервал до атаки для фонового обучения")
    train_end = minimum + int((first_attack - minimum) * 0.6)
    calibration_end = minimum + int((first_attack - minimum) * 0.8)
    for event in events:
        event["phases"] = ";".join(map(str, sorted(event["phases"])))
        event["reference_label"] = (
            "attack" if event["attack_packets"] else "background_reference"
        )
        event["split"] = partition(
            event["start_us"], event["end_us"], train_end, calibration_end
        )
    save_csv(OUT / "events.csv", events)
    summary = {
        "packets": number,
        "decoded_packets": sum(e["packets"] for e in events),
        "excluded_packets": dict(excluded),
        "flows": len(events),
        "flow_labels": dict(Counter(e["reference_label"] for e in events)),
        "annotation_packet_counts": dict(label_counts),
        "matched_packet_counts": dict(matched),
        "unique_annotation_fingerprints": len(labels),
        "matched_unique_fingerprints": len(matched_fingerprints),
        "minimum_us": minimum,
        "maximum_us": maximum,
        "first_attack_us": first_attack,
        "train_end_us": train_end,
        "calibration_end_us": calibration_end,
    }
    save_json(OUT / "source_summary.json", summary)
    print(
        f"Прочитано {number} пакетов; подготовлено {len(events)} транспортных событий",
        flush=True,
    )


if __name__ == "__main__":
    main()
