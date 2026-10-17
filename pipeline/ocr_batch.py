import argparse, base64, json, os, sys, time, threading, random
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import Declaration, PROMPT
from google import genai
from google.genai import types

lock = threading.Lock()

def ocr_one(client, seq, pdf_dir, out_dir, model, resolution, retries=4):
    out = os.path.join(out_dir, f"{seq}.json")
    if os.path.exists(out) and not getattr(ocr_one, 'force', False): return seq, True, 'skip'
    group = getattr(ocr_one, 'groups', {}).get(seq)
    paths = [os.path.join(pdf_dir, f) for f in group['files']] if group else [os.path.join(pdf_dir, f"{seq}.pdf")]
    docs = []
    for path in paths:
        doc = {"type": "document", "data": base64.b64encode(open(path, 'rb').read()).decode(), "mime_type": "application/pdf"}
        if resolution: doc["resolution"] = resolution
        docs.append(doc)
    prompt = PROMPT if len(docs) == 1 else PROMPT + "\n- 本次共有 %d 份 PDF：第一份是原申報表，其後是「補正」文件（補1、補2…）。補正內容取代原表的對應項目，請輸出合併補正後的最終申報內容，並在 notes 說明補正了什麼。" % len(docs)
    last = None
    for attempt in range(retries):
        try:
            t = time.time()
            if getattr(ocr_one, 'vertex', False):
                from google.genai import types
                parts = [types.Part.from_bytes(data=base64.b64decode(x["data"]), mime_type="application/pdf") for x in docs]
                r = client.models.generate_content(model=model, contents=parts + [prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=Declaration.model_json_schema()))
                text = r.text; um = r.usage_metadata
                usage = {"in": um.prompt_token_count, "out": um.candidates_token_count, "thought": um.thoughts_token_count}
            else:
                it = client.interactions.create(
                    model=model,
                    input=docs + [{"type": "text", "text": prompt}],
                    response_format={"type": "text", "mime_type": "application/json", "schema": Declaration.model_json_schema()},
                )
                text = it.output_text
                usage = {"in": it.usage.total_input_tokens, "out": it.usage.total_output_tokens, "thought": it.usage.total_thought_tokens}
            d = Declaration.model_validate_json(text)
            rec = {"seq": seq, "model": model, "resolution": resolution, "elapsed": round(time.time()-t,1),
                   "via": "vertex" if getattr(ocr_one, 'vertex', False) else "gemini-api", "usage": usage, "data": d.model_dump()}
            tmp = out + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f: json.dump(rec, f, ensure_ascii=False)
            os.replace(tmp, out)
            return seq, True, rec["elapsed"]
        except Exception as e:
            last = repr(e)[:300]
            time.sleep(min(60, (2 ** attempt) * 3 + random.random() * 3))
    with lock:
        with open(os.path.join(out_dir, "_errors.log"), 'a') as f: f.write(f"{seq}\t{model}\t{last}\n")
    return seq, False, last

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", default="data/source_pdfs/y115"); ap.add_argument("--out-dir", required=True)
    ap.add_argument("--workers", type=int, default=10); ap.add_argument("--model", default="gemini-3.5-flash")
    ap.add_argument("--resolution", default=None); ap.add_argument("--only", default=None, help="file with seq list")
    ap.add_argument("--extra", action="store_true"); ap.add_argument("--force", action="store_true"); ap.add_argument("--groups", default=None); ap.add_argument("--vertex", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    if a.groups: ocr_one.groups = json.load(open(a.groups, encoding='utf-8'))
    if a.groups and not a.only: seqs = sorted(s for s, g in ocr_one.groups.items() if all(os.path.exists(os.path.join(a.pdf_dir, f)) for f in g['files']))
    elif a.only: seqs = [l.strip() for l in open(a.only) if l.strip()]
    else: seqs = sorted(f[:-4] for f in os.listdir(a.pdf_dir) if f.endswith('.pdf'))
    if not a.force: seqs = [s for s in seqs if not os.path.exists(os.path.join(a.out_dir, f"{s}.json"))]
    print(f"todo {len(seqs)}", flush=True)
    ocr_one.force = a.force
    ocr_one.vertex = a.vertex
    if a.extra:
        import schema_extra
        globals()['Declaration'] = schema_extra.Extra; globals()['PROMPT'] = schema_extra.PROMPT_EXTRA
    client = genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"], location="global", http_options=types.HttpOptions(timeout=400000)) if a.vertex else genai.Client()
    done = ok = 0; t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(ocr_one, client, s, a.pdf_dir, a.out_dir, a.model, a.resolution) for s in seqs]
        for f in as_completed(futs):
            seq, good, info = f.result(); done += 1; ok += good
            if done % 10 == 0 or not good:
                print(f"[{done}/{len(seqs)}] ok={ok} {seq} {'OK' if good else 'FAIL'} {info} elapsed={time.time()-t0:.0f}s", flush=True)
    print(f"finished ok={ok}/{len(seqs)} in {time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__": main()
