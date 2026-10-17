"""Pack the source PDFs into per-year, per-city zip files for a GitHub Release and write data/pdf_manifest.csv.

python pipeline/make_release.py            # writes release/*.zip and data/pdf_manifest.csv
Upload (maintainers): gh release create <tag> release/*.zip --title ... --notes-file release/NOTES.md
"""
import csv, hashlib, os, sys, zipfile
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths

OUT = os.path.join(paths.ROOT, 'release')


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()


def main():
    os.makedirs(OUT, exist_ok=True)
    meta = paths.candidates_index(); groups = paths.groups_111()
    tag = paths.CONFIG['pdf_release_tag']; repo = paths.CONFIG['repo']
    rows, zips = [], {}
    for seq in sorted(meta):
        p = os.path.join(paths.PDFS, 'y115', f'{seq}.pdf')
        if os.path.exists(p):
            z = f"pdf-115-{meta[seq]['City_ID']}.zip"
            zips.setdefault(z, []).append((p, f'{seq}.pdf'))
            rows.append(dict(year=115, seq=seq, name=meta[seq]['NAME'], city=meta[seq]['City_ID_NM'], zip=z, file_in_zip=f'{seq}.pdf',
                             original_source=paths.YEARS['115']['pdf_url'].format(seq=seq), original_path='', bytes=os.path.getsize(p), sha256=sha256(p)))
    for seq, g in sorted(groups.items()):
        for f, orig in zip(g['files'], g['paths']):
            p = os.path.join(paths.PDFS, 'y111', f)
            if not os.path.exists(p): continue
            z = f"pdf-111-{meta[seq]['City_ID']}.zip"
            zips.setdefault(z, []).append((p, f))
            src = f"https://web.archive.org/web/2023/https://111declaration.cec.gov.tw/{quote(orig)}"
            rows.append(dict(year=111, seq=seq, name=meta[seq]['NAME'], city=meta[seq]['City_ID_NM'], zip=z, file_in_zip=f,
                             original_source=src, original_path=orig, bytes=os.path.getsize(p), sha256=sha256(p)))
    for z, files in sorted(zips.items()):
        zp = os.path.join(OUT, z)
        with zipfile.ZipFile(zp, 'w', compression=zipfile.ZIP_STORED) as zf:  # PDFs are already compressed
            for p, name in files: zf.write(p, name)
        print(f'{z}: {len(files)} files, {os.path.getsize(zp) / 1e6:.0f} MB')
    with open(os.path.join(paths.DATA, 'pdf_manifest.csv'), 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    with open(os.path.join(OUT, 'NOTES.md'), 'w', encoding='utf-8') as fh:
        fh.write(f"候選人財產申報表原始 PDF。\n\n- 115 年：中選會公職候選人財產申報系統（公告端）下載，{sum(1 for r in rows if r['year'] == 115)} 份。\n"
                 f"- 111 年：網際網路檔案館保存的 111declaration.cec.gov.tw 存檔，只收與 115 年候選人同縣市同姓名者，{sum(1 for r in rows if r['year'] == 111)} 個檔案（含補正）。\n\n"
                 f"每個 zip 是一個年度、一個縣市（檔名中的數字是中選會縣市代碼）。檔案對照與 SHA-256 檢查碼見 repo 的 data/pdf_manifest.csv。\n")
    print(len(rows), 'files ->', len(zips), 'zips; manifest written; upload with:')
    print(f'  gh release create {tag} release/*.zip --repo {repo} --title "原始申報表 PDF" --notes-file release/NOTES.md')


if __name__ == '__main__':
    main()
