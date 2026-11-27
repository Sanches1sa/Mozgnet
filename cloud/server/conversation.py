"""Shared conversational answer policy for cloud chat and messaging bots."""
import re
import store


def prefer_web(text):
    return bool(re.search(r'\b(найди|найти|поищи|проверь|интернет|свежие|новости|сейчас|сегодня|актуальн)',str(text),re.I))


def usable_source(source):
    return bool(source and source.get('text') and not re.search(r'^(не знаю|пока не знаю|не нашл)',str(source.get('text')).strip(),re.I))


def answer(text, *, ctx=None, sender='владелец', deliver=None, long_mode=False, no_web=False):
    import brain
    import answer_focus
    original_question=text
    text=answer_focus.context_question(text,ctx)
    clarify=answer_focus.clarification(text,ctx)
    if clarify:return clarify,brain.Z['sys'],{'clarification':True,'missing_fields':['device_model']}
    style=store.settings().get('conversation_style','normal')
    if style=='detailed': long_mode=True
    elif style=='short': long_mode=False
    reply, zone, info = brain.think(text,long_mode,route='cloud',sender=sender,ctx=ctx,deliver=None if answer_focus.practical(text) else deliver,no_web=no_web)
    info=dict(info or {})
    if text!=original_question:info['resolved_question']=text
    import answer_focus
    original_reply=str(reply or '')
    reply=answer_focus.clean(text,reply)
    if answer_focus.hardware(text) and info.get('sources'):
        info['sources']=answer_focus.sources_for(text,info['sources'])
        if not info['sources']:info['grounded']=False
        if answer_focus.specification(text):
            excerpt,supported=answer_focus.source_excerpt(text,info['sources'])
            if excerpt:reply=excerpt;info['sources']=supported;info['source_excerpt']=True
            elif not info.get('honest'):reply=''
    grounded=bool(info.get('sources') or info.get('honest') or info.get('grounded'))
    off_topic=answer_focus.practical(text) and (not answer_focus.usable(text,reply,grounded=grounded) or ((bool(info.get('mem_brain')) or answer_focus.hardware(text)) and not grounded))
    if off_topic:info['focus_rejected']=True
    if original_reply!=reply:info['focus_cleaned']=True
    settings=store.settings()
    allowed=not no_web and settings.get('web_on',True) and settings.get('conversation_web',True)
    weak=off_topic or not str(reply or '').strip() or bool(re.search(r'^(?:\s*\u2063)?\s*(?:не знаю|пока не знаю|пока не наш|не нашл|ничего не наш|материалов по теме пока нет|облако сейчас без интернета)',str(reply or ''),re.I))
    weak=weak or (not info.get('sources') and bool(re.search(r'нет.{0,50}подтвержд[её]нной памяти|нет ни статьи в базе знаний',str(reply or ''),re.I)))
    try:
        import unify
        weak=weak or unify.is_weak(reply,zone)
        import smalltalk
        if smalltalk.kind_of(text): allowed=False
    except Exception: pass
    if allowed and (weak or (prefer_web(text) and not info.get('sources'))):
        import webfetch
        query=str(text)
        if len(query.split())<4 and re.search(r'\b(она|он|это|ещё|еще|почему|когда)\b',query,re.I):
            previous=next((str(m.get('text') or '') for m in reversed(ctx or []) if m.get('role')=='user' and m.get('text')!=text),'')
            query=(previous+' '+query).strip()[:2000]
        info['web_retry']=True
        try:
            result, meta=webfetch.alice(query,long_mode=long_mode)
            meta=meta or {}
            sources=meta.get('sources') or []
            if answer_focus.hardware(text):
                sources=answer_focus.sources_for(text,sources)
                if not answer_focus.usable(text,result,grounded=True):
                    result,excerpt_sources=answer_focus.source_excerpt(text,sources)
                    if excerpt_sources:sources=excerpt_sources
            if answer_focus.command_question(text) and sources and not answer_focus.usable(text,result,grounded=True):
                for source in sources[:2]:
                    if not isinstance(source,dict) or not source.get('url'):continue
                    ok,page=webfetch.fetch_page(source['url'],timeout=8,max_bytes=240000)
                    if not ok or not isinstance(page,dict):continue
                    quote=answer_focus.command_from_page(text,page)
                    if quote:
                        result=quote
                        sources=[source]
                        info['command_extracted_from_source']=True
                        break
            if result and sources and (not answer_focus.practical(text) or answer_focus.usable(text,result,grounded=True)):
                reply,zone=result,brain.Z['web']
                info.update(web=meta.get('mode') or 'search',sources=sources,grounded=True)
                off_topic=False
            else:
                info['web_errors']=meta.get('errors') or webfetch.engines_diag(3)
        except Exception as exc:
            info['web_errors']=[webfetch.honest(exc)]
    reply=answer_focus.clean(text,reply)
    if off_topic:
        reply='Не удалось найти подтверждённый ответ по этому вопросу.'
        info['focus_no_answer']=True
    if style=='short' and zone not in (brain.Z['code'],brain.Z['doc']):
        import textutils
        reply=textutils.cut_at_sentence(str(reply),900)
    if settings.get('conversation_sources',True) and info.get('sources'):
        links=[]
        for source in info['sources'][:3]:
            url=source.get('url','') if isinstance(source,dict) else ''
            if url.startswith(('https://','http://')) and url not in str(reply):
                links.append(url)
        if links:reply=str(reply).rstrip()+'\n\nИсточники:\n'+'\n'.join(links)
    return reply,zone,info
