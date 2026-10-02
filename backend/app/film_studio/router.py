"""FastAPI routes for the film studio (Flask → FastAPI port)."""

from __future__ import annotations

import json
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.film_studio import service
from app.film_studio.settings import get_film_settings

router = APIRouter(tags=["film-studio"])

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>AI Film Studio</title>
<style>body{font-family:system-ui;background:#0f0f1a;color:#eee;max-width:980px;margin:30px auto;padding:0 16px}
textarea,input,select{width:100%;box-sizing:border-box;background:#1a1a2e;color:#eee;border:1px solid #444;border-radius:8px;padding:10px;font-size:15px;margin-top:6px}
textarea{height:130px}label{display:block;margin-top:14px;font-weight:600}small{color:#aaa;font-weight:400}
button{background:#6c5ce7;color:#fff;border:0;padding:9px 14px;border-radius:8px;font-size:14px;cursor:pointer;margin:6px 6px 0 0}
button:disabled{opacity:.5}video,img{width:100%;border-radius:8px}.err{color:#ff7675}.warn{color:#fdcb6e;font-size:13px}
.row{display:flex;gap:10px}.row>div{flex:1}.card{background:#16162a;border:1px solid #333;border-radius:10px;padding:12px;margin-top:12px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:8px 0}.tag{font-size:12px;background:#2d2d52;padding:2px 8px;border-radius:10px}
.good{color:#55efc4}.bad{color:#ff7675}#retry{background:#e17055}</style>
<h2>AI Film Studio</h2>
<label>Story / idea <small>(bas itna likho. Characters, scenes, awaaz, SFX, music sab auto)</small></label>
<textarea id=story placeholder="Ramayan ka short cinematic trailer: Hanuman ka Lanka dahan"></textarea>
<div class=row><div><label>Scenes (max __MAX__)</label><input id=n type=number value=2 min=1 max=__MAX__></div>
<div><label>Language</label><select id=lang>
<option>Hindi</option>
<option>English (India)</option>
<option>Bengali</option>
<option>Gujarati</option>
<option>Kannada</option>
<option>Malayalam</option>
<option>Marathi</option>
<option>Odia</option>
<option>Punjabi</option>
<option>Tamil</option>
<option>Telugu</option>
</select></div></div>
<button id=go onclick=go()>Generate Film</button>
<button id=retry style="display:none" onclick=retryFailed()>Retry failed scenes</button>
<div id=st style="margin-top:14px"></div><div id=wr class=warn></div><div id=out></div><div id=cards></div>
<script>
let JID=null,sig={},timer=null;
const BASE="__BASE__";
const $=id=>document.getElementById(id);
function esc(x){return String(x==null?"":x).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function startPoll(){if(timer)clearInterval(timer);timer=setInterval(poll,3000);poll()}
async function go(){$('go').disabled=true;$('retry').style.display='none';$('out').innerHTML="";$('cards').innerHTML="";$('st').innerHTML="";$('wr').innerText="";sig={};
const r=await fetch(BASE+"/start",{method:"POST",headers:{"Content-Type":"application/json"},
body:JSON.stringify({story:$('story').value,n:+$('n').value,lang:$('lang').value})});
const d=await r.json();if(d.error){$('st').innerHTML='<span class=err>'+esc(d.error)+'</span>';$('go').disabled=false;return}
JID=d.id;history.replaceState(null,"","?job="+JID);startPoll()}
function card(s,i){const u=BASE+'/media/'+JID+'/';const v='?v='+s.ver;
const bad=s.status!=='done';
return '<b>Scene '+(i+1)+'</b> <span class="tag '+(s.status==='error'?'bad':'')+'">'+esc(s.status)+'</span> <small>mood: '+esc(s.mood)+' | sfx: '+esc(s.sfx_prompt)+'</small>'+
'<div class=cols><div>'+(s.key?'<img src="'+u+s.key+v+'">':'')+'</div><div>'+(s.clip?'<video controls muted src="'+u+s.clip+v+'"></video>':'')+'</div></div>'+
'<small>'+esc(s.action)+'</small>'+
'<input id=note'+i+' placeholder="Kya galat hai? (optional)">'+
(bad?'<button onclick="rg('+i+',\\'auto\\')">Retry this scene (resume)</button>':'')+
'<button onclick="rg('+i+',\\'keyframe\\')">Redo character + video</button>'+
'<button onclick="rg('+i+',\\'video\\')">Redo video only</button>'+
'<button onclick="rg('+i+',\\'audio\\')">Redo voice + sfx</button>'}
async function rg(i,level){const note=$('note'+i).value;
const r=await (await fetch(BASE+'/regen/'+JID+'/'+i,{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({level:level,note:note})})).json();
if(r.ok===false){alert(r.msg||'busy');return}
$('out').innerHTML="";sig.f=null;$('go').disabled=true;$('retry').style.display='none';startPoll()}
async function retryFailed(){
const r=await (await fetch(BASE+'/retry/'+JID,{method:'POST'})).json();
if(!r.ok){alert(r.msg||'Retry ke liye kuch nahi');return}
$('out').innerHTML="";sig.f=null;$('go').disabled=true;$('retry').style.display='none';startPoll()}
async function poll(){
let s;try{s=await (await fetch(BASE+"/status/"+JID)).json()}catch(e){return}
const done=(s.stage==="Ready"||s.stage==="Error");
$('st').innerHTML='<b>'+esc(s.title||"")+'</b> - '+esc(s.stage)+' | approx cost $'+(s.cost||0)+(s.error?' <span class=err>'+esc(s.error)+'</span>':'');
$('wr').innerText=(s.warnings||[]).join(" | ");
(s.scenes||[]).forEach((c,i)=>{const k=c.status+c.ver;let el=$('c'+i);
if(!el){el=document.createElement('div');el.className='card';el.id='c'+i;$('cards').appendChild(el)}
if(sig[i]!==k){const keep=$('note'+i)?$('note'+i).value:"";sig[i]=k;el.innerHTML=card(c,i);if(keep)$('note'+i).value=keep}});
const bad=(s.scenes||[]).filter(c=>c.status!=='done').length;
$('retry').innerText='Retry failed scenes ('+bad+')';
$('retry').style.display=(done&&bad)?'inline-block':'none';
if(s.stage==="Ready"&&s.film_ver){const k='film'+s.film_ver;if(sig.f!==k){sig.f=k;
$('out').innerHTML='<h3>Final film</h3><video controls src="'+BASE+'/media/'+JID+'/film.mp4?v='+s.film_ver+'"></video><p><a style=color:#a29bfe href="'+BASE+'/media/'+JID+'/film.mp4?dl=1">Download MP4</a></p>'}}
if(done){$('go').disabled=false;clearInterval(timer);timer=null}}
const q=new URLSearchParams(location.search).get('job');if(q){JID=q;startPoll()}
</script>"""


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def studio_index() -> HTMLResponse:
    max_scenes = get_film_settings().film_studio_max_scenes
    html = PAGE.replace("__MAX__", str(max_scenes)).replace("__BASE__", "/studio")
    return HTMLResponse(html)


@router.post("/start")
def start(body: dict[str, Any]) -> dict[str, Any]:
    miss = service.missing_keys()
    if miss:
        return {"error": ".env mein set karo: " + ", ".join(miss)}
    if not shutil.which("ffmpeg"):
        return {"error": "ffmpeg install nahi hai: sudo apt install ffmpeg"}
    story = str(body.get("story") or "").strip()
    if len(story) < 5:
        return {"error": "Story/idea likho"}
    try:
        n = int(body.get("n", 8))
    except (TypeError, ValueError):
        n = 8
    lang = str(body.get("lang") or "Hindi")
    j = service.create_and_start_job(story, n, lang)
    return {"id": j["id"]}


@router.get("/status/{jid}")
def status(jid: str) -> Response:
    j = service.get_job(jid)
    if not j:
        return Response(
            json.dumps({"error": "not found", "stage": "Error"}),
            media_type="application/json",
        )
    with service.lock:
        return Response(
            json.dumps(j, ensure_ascii=False, default=str),
            media_type="application/json",
        )


@router.post("/regen/{jid}/{i}")
def regen(jid: str, i: int, body: dict[str, Any] | None = None) -> dict[str, Any]:
    import threading

    j = service.get_job(jid)
    if not j or i >= len(j["scenes"]):
        raise HTTPException(status_code=404, detail="Not found")
    if (j["id"], i) in service.running:
        return {"ok": False, "msg": "Ye scene abhi ban rahi hai, wait karo"}
    b = body or {}
    level = b.get("level") if b.get("level") in ("auto", "keyframe", "video", "audio") else "keyframe"
    if level == "audio" and not j["scenes"][i]["clip"]:
        level = "auto"
    j["scenes"][i]["status"] = "queued"
    j["error"], j["stage"] = None, f"Scene {i + 1} dobara ban rahi hai"
    note = str(b.get("note") or "")[:300]
    threading.Thread(
        target=service.run_regen,
        args=(j, i, level, note),
        daemon=True,
    ).start()
    return {"ok": True}


@router.post("/retry/{jid}")
def retry(jid: str) -> dict[str, Any]:
    import threading

    j = service.get_job(jid)
    if not j:
        raise HTTPException(status_code=404, detail="Not found")
    if not j["scenes"]:
        return {"ok": False, "msg": "Script hi nahi bani thi, naya Generate karo"}
    targets = [
        i
        for i, s in enumerate(j["scenes"])
        if s["status"] != "done" and (j["id"], i) not in service.running
    ]
    if not targets:
        return {"ok": False, "msg": "Sab scenes done hain ya chal rahi hain"}
    with service.lock:
        for i in targets:
            j["scenes"][i]["status"] = "queued"
        j["warnings"] = [
            w
            for w in j["warnings"]
            if not any(w.startswith(f"Scene {i + 1}") for i in targets)
        ]
        j["error"], j["stage"] = None, f"{len(targets)} adhoori scenes retry ho rahi hain"
    service.save(j)
    threading.Thread(target=service.run_retry, args=(j, targets), daemon=True).start()
    return {"ok": True, "n": len(targets)}


@router.get("/media/{jid}/{name}")
def media(jid: str, name: str, request: Request) -> FileResponse:
    path = service.media_path(jid, name)
    if path is None:
        raise HTTPException(status_code=404, detail="Not found")
    as_attachment = bool(request.query_params.get("dl"))
    return FileResponse(
        path,
        filename=f"film_{jid}.mp4" if name == "film.mp4" else name,
        media_type="video/mp4" if name.endswith(".mp4") else "image/jpeg",
        content_disposition_type="attachment" if as_attachment else "inline",
    )
