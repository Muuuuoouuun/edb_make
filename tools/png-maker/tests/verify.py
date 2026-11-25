from pathlib import Path
import json,zipfile,hashlib,os
from PIL import Image,ImageChops
root=Path(os.environ["PNG_MAKER_TEST_DIR"])
records=[]
for tag in ("lined","plain"):
    folder=root/tag
    src=Image.open(folder/"분수 연습.v2.png").convert("RGBA")
    for width in (1440,390):
        run=folder/str(width);ui=json.loads((run/"ui.json").read_text(encoding="utf8"))
        names=ui["singleNames"];assert len(names)==7 and len(set(names))==7
        files={};pixels=0
        for phase,snapshot in [("before",ui["before"]),("final",ui["after"])]:
            extracted=run/phase/"unpacked";extracted.mkdir(exist_ok=True)
            with zipfile.ZipFile(run/phase/ui["zip"]) as z:
                assert z.namelist()==names and z.testzip() is None
                for i,name in enumerate(names):
                    assert Path(name).name==name and z.getinfo(name).flag_bits & 0x800
                    f=Path(z.extract(name,extracted))
                    x,y,w,h=snapshot[i]["rect"]
                    with Image.open(f) as crop:
                        assert crop.size==(w,h)
                        assert ImageChops.difference(src.crop((x,y,x+w,y+h)).convert("RGB"),crop.convert("RGB")).getbbox() is None
                        assert src.crop((x,y,x+w,y+h)).tobytes()==crop.convert("RGBA").tobytes()
                    if phase=="final":
                        assert f.read_bytes()==(run/"single"/name).read_bytes()
                        assert snapshot[i]["rect"]==ui["truth"][i];pixels+=w*h
                    files[(phase,i)]=hashlib.sha256(f.read_bytes()).hexdigest()
        for i in [0,5,6]:
            assert files["before",i]==files["final",i] and ui["before"][i]["id"]==ui["after"][i]["id"]
        for i in [2,3]:assert files["before",i]!=files["final",i]
        records.append({"page":tag,"width":width,"zip":ui["zip"],"question_pngs":7,"source_pixels":pixels,
                        "outside_numbers":[1,6,7],"outside_ids_and_png_bytes_equal":True,"all_single_pngs_equal_extracted":True,"missing":0,"clipped":0,
                        "zip_sha256":hashlib.sha256((run/"final"/ui["zip"]).read_bytes()).hexdigest()})
for tag in ["lined","plain"]:
    assert len({r["zip_sha256"] for r in records if r["page"]==tag})==1
(root/"verified.json").write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding="utf8")
print([(r["page"],r["width"],r["source_pixels"]) for r in records])
