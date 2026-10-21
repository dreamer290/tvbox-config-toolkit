import json,re,ssl,urllib.request,urllib.error,time

_ctx=ssl.create_default_context(); _ctx.check_hostname=False; _ctx.verify_mode=ssl.CERT_NONE
UA="okhttp/3.15"

def http_get(url,timeout=15,maxbytes=400000,headers=None,method="GET"):
    h={"User-Agent":UA}
    if headers: h.update(headers)
    req=urllib.request.Request(url,headers=h,method=method)
    t0=time.time()
    try:
        with urllib.request.urlopen(req,timeout=timeout,context=_ctx) as r:
            data=r.read(maxbytes)
            return dict(ok=True,code=r.status,data=data,ct=r.headers.get("Content-Type",""),
                        final=r.geturl(),ms=int((time.time()-t0)*1000),err="")
    except urllib.error.HTTPError as e:
        return dict(ok=False,code=e.code,data=b"",ct="",final=url,ms=int((time.time()-t0)*1000),err=f"HTTP {e.code}")
    except Exception as e:
        return dict(ok=False,code=0,data=b"",ct="",final=url,ms=int((time.time()-t0)*1000),
                    err=f"{type(e).__name__}: {str(e)[:90]}")

def lenient_json(raw):
    txt=raw.decode("utf-8","ignore").lstrip("\ufeff")
    # drop leading // comment lines before first {
    i=txt.find("{")
    if i<0: raise ValueError("no json object")
    body=txt[i:]
    # strip full-line comments outside strings (crude but effective for these files)
    lines=[]
    for ln in body.splitlines():
        s=ln.strip()
        if s.startswith("//"): continue
        lines.append(ln)
    body="\n".join(lines)
    for attempt in (body, re.sub(r",(\s*[}\]])",r"\1",body)):
        try: return json.loads(attempt,strict=False)
        except Exception: pass
    # last resort: cut at last }
    j=body.rfind("}")
    try: return json.loads(re.sub(r",(\s*[}\]])",r"\1",body[:j+1]),strict=False)
    except Exception as e: raise
