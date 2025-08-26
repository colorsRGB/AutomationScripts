#!/usr/bin/env python3
# har_chat_signalr_audit.py
# Usage: python har_chat_signalr_audit.py file.har -o ./out
import argparse, json, re
from pathlib import Path
from datetime import datetime
import pandas as pd

REC_SEP = "\x1e"  # SignalR record separator

def parse_json_safe(txt):
    try:
        return json.loads(txt)
    except Exception:
        return None

def iso_parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(re.sub("Z$", "+00:00", ts))
    except Exception:
        return None

def load_har(path: Path):
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return json.load(f)

def extract_api_chats(entries):
    rows = []
    for e in entries:
        req = e.get("request", {}) or {}
        res = e.get("response", {}) or {}
        url = (req.get("url") or "")
        method = req.get("method")
        if method not in ("GET", "POST"):
            continue
        if "unassigned" not in url and "ongoing" not in url:
            continue
        text = ((res.get("content") or {}).get("text")) or ""
        data = parse_json_safe(text)
        # try to find list of items
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            if isinstance(data.get("items"), list):
                items = data["items"]
            elif isinstance(data.get("data"), list):
                items = data["data"]
            else:
                # any list<dict> inside dict
                for k, v in data.items():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        items = v
                        break
        list_name = "unassigned" if "unassigned" in url else ("ongoing" if "ongoing" in url else "unknown")
        for it in items:
            chat_id = str(it.get("id") or it.get("chatId") or it.get("chat_id") or "")
            status = it.get("status") or it.get("state")
            rows.append({
                "list": list_name,
                "chatId": chat_id,
                "status": status,
                "source_url": url
            })
    df = pd.DataFrame(rows).drop_duplicates().reset_index(drop=True)
    return df

def extract_from_signalr_part(part_dict):
    """
    Unwraps {"type":1,"target":"SendEvent","arguments":[{...actual event...}]}
    Returns (event_payload, fallback_event_type)
    """
    if not isinstance(part_dict, dict):
        return None, None
    if part_dict.get("target") == "SendEvent":
        args = part_dict.get("arguments") or []
        if args and isinstance(args[0], dict):
            return args[0], "SendEvent"
    return part_dict, part_dict.get("target") or None

def guess_chat_id(payload):
    if not isinstance(payload, dict):
        return None
    # common variants
    for k in ["chatId","ChatId","conversationId","id","Id","ticketId","TicketId","dialogId","DialogId"]:
        v = payload.get(k)
        if isinstance(v, (str,int)):
            return str(v)
    # nested chat.id
    cur = payload
    for path in (["payload","chat","id"], ["data","chat","id"], ["body","chat","id"]):
        cur = payload
        ok = True
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False; break
        if ok and isinstance(cur, (str,int)):
            return str(cur)
    return None

def guess_event_type(payload, fallback=None):
    if isinstance(payload, dict):
        for k in ["type","Type","event","Event","messageType","MessageType","name"]:
            v = payload.get(k)
            if isinstance(v, str):
                return v
    return fallback or "unknown"

def extract_ws_events(entries):
    rows = []
    for e in entries:
        req = e.get("request", {}) or {}
        url = (req.get("url") or "")
        if "agent-events-hub" not in url:
            continue
        msgs = e.get("_webSocketMessages") or e.get("webSocketMessages") or []
        for m in msgs:
            if m.get("type") != "receive":
                continue
            data = m.get("data") or ""
            parts = [p for p in data.split(REC_SEP) if p.strip()]
            for part in parts:
                d = parse_json_safe(part)
                if not d:
                    continue
                payload, fb = extract_from_signalr_part(d)
                chat_id = guess_chat_id(payload)
                etype = guess_event_type(payload, fb)
                tstr = m.get("time") or e.get("startedDateTime")
                ts = iso_parse(tstr)
                rows.append({
                    "timestamp": ts.isoformat() if ts else None,
                    "chatId": chat_id,
                    "eventType": etype,
                    "socket_url": url,
                    "raw": payload
                })
    return pd.DataFrame(rows).reset_index(drop=True)

def summarize_ws(df):
    rows = []
    if df is None or df.empty or "chatId" not in df.columns:
        return pd.DataFrame(columns=["chatId","events"])
    per = {}
    for _, r in df.iterrows():
        cid = r.get("chatId")
        et = r.get("eventType") or "unknown"
        if not cid:
            continue
        per.setdefault(cid, set()).add(et)
    for cid, types in per.items():
        rows.append({"chatId": cid, "events": ", ".join(sorted(types))})
    return pd.DataFrame(rows).sort_values("chatId").reset_index(drop=True)

def compare_api_ws(api_df, ws_df):
    api_ids = set(api_df["chatId"].dropna().astype(str)) if ("chatId" in api_df.columns and not api_df.empty) else set()
    ws_ids  = set(ws_df["chatId"].dropna().astype(str))   if ("chatId" in ws_df.columns and not ws_df.empty) else set()
    only_api = sorted([cid for cid in api_ids if cid not in ws_ids])
    only_ws  = sorted([cid for cid in ws_ids if cid not in api_ids])
    return pd.DataFrame({"onlyInAPI": pd.Series(only_api, dtype="string"),
                         "onlyInWS": pd.Series(only_ws,  dtype="string")})

def main():
    ap = argparse.ArgumentParser(description="Audit chats and SignalR events from HAR")
    ap.add_argument("har", type=Path, help="Path to HAR file")
    ap.add_argument("-o", "--outdir", type=Path, default=Path("./out"), help="Output directory")
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    har = load_har(args.har)
    entries = (har.get("log") or {}).get("entries") or []

    api_df = extract_api_chats(entries)
    ws_df  = extract_ws_events(entries)
    ws_sum = summarize_ws(ws_df)
    cmp_df = compare_api_ws(api_df, ws_df)

    api_csv = args.outdir / "api_chats_unassigned_ongoing.csv"
    ws_csv  = args.outdir / "ws_agent_events_parsed.csv"
    sum_csv = args.outdir / "ws_chat_event_summary_parsed.csv"
    cmp_csv = args.outdir / "api_vs_ws_compare.csv"

    api_df.to_csv(api_csv, index=False)
    ws_df.to_csv(ws_csv, index=False)
    ws_sum.to_csv(sum_csv, index=False)
    cmp_df.to_csv(cmp_csv, index=False)

    print(f"Saved:\n- {api_csv}\n- {ws_csv}\n- {sum_csv}\n- {cmp_csv}")
    print(f"Counts: API rows={len(api_df)}, WS rows={len(ws_df)}, WS chats={len(ws_sum)}")

if __name__ == "__main__":
    main()
