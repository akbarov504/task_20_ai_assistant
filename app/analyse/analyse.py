from pathlib import Path
from app.analyse.providers import get_adapter
from app.analyse.schemas import AskResponse
from app.analyse.providers import ProviderName
from app.analyse.prompts import ANALYZE_SYSTEM_PROMPT

async def analyse(file_path, provider='gemini',model= 'gemini-3.7-flash',  mime_type='audio/mpeg'):

    path =  Path(file_path)
    prompt = ANALYZE_SYSTEM_PROMPT
    if provider is None:
        provider = 'gemini'
    
    if mime_type is None:
        mime_type =  'audio/mpeg'
    try:
        adapter = get_adapter(provider)
        remote = await adapter.upload(path, mime_type)
        answer = await adapter.ask(remote=remote, prompt=prompt, model=model)
        return AskResponse(provider=provider, model=model, answer=answer)
    finally:
        print('done')
        # path.unlink(missing_ok=True)
