"""BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE entrypoint."""
from __future__ import annotations
from football_intelligence_agent_v2 import answer, match_answer

ENGINE_NAME='BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE'
ENGINE_VERSION='0.2.0'

def patch_main(main):
    from fastapi import Request, HTTPException
    from fastapi.routing import APIRoute
    async def chat(request: Request):
        try: p=await request.json()
        except Exception: p={}
        msg=str(p.get('message') or p.get('question') or '').strip()
        if not msg: raise HTTPException(400,'Mesaj boş olamaz.')
        return await answer(main,msg,p.get('history') or [])
    async def mch(request: Request, match_id: int):
        try: p=await request.json()
        except Exception: p={}
        msg=str(p.get('message') or p.get('question') or '').strip()
        if not msg: raise HTTPException(400,'Mesaj boş olamaz.')
        return await match_answer(main,match_id,msg,p.get('history') or [])
    main.app.router.routes=[r for r in main.app.router.routes if not(isinstance(r,APIRoute) and r.path in ('/chat','/matches/{match_id}/chat') and 'POST' in (r.methods or set()))]
    main.app.add_api_route('/chat',chat,methods=['POST'])
    main.app.add_api_route('/matches/{match_id}/chat',mch,methods=['POST'])
