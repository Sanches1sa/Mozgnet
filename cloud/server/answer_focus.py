"""Question-scoped filtering; no canned command answers or invented facts."""
import re
import textutils

_STOP=set('какой какая какое какие как что это для при чтобы можно нужно надо мне пожалуйста подскажи напиши скажи команда команды команду командой система системы терминал консоль инструкция ответ вопрос действие способ сделать работает'.split())

def _normal(text):
    text=str(text or '').lower().replace('ё','е')
    text=re.sub(r'\b(?:виндовс|виндоус|виндовз|windows|win(?:dows)?\s*\d+)\b',' windows ',text)
    text=re.sub(r'\b(?:перезагруз\w*|перезапуск\w*|перезапуст\w*|reboot|restart)\b',' restart ',text)
    text=re.sub(r'\b(?:озу|ram|оперативн\w*\s+памят\w*)\b',' ram ',text)
    return text


def topics(text):
    return {textutils.stem(w) for w in re.findall(r'[а-яa-z0-9_-]{3,}',_normal(text)) if w not in _STOP}


def command_question(q):
    if re.search(r'\b(?:что такое|что делает|что означает|как работает|объясни)\b',str(q),re.I):return False
    return bool(re.search(r'\b(?:команд\w*|cmd|powershell|терминал\w*|консол\w*)\b',str(q),re.I))


def practical(q):
    if re.search(r'\b(?:сказк\w*|стих\w*|придумай|фантаз\w*|анекдот\w*)',str(q),re.I):return False
    return hardware(q) or bool(re.search(r'\b(?:ошибк\w*|настройк\w*|драйвер\w*|сервер\w*|процессор\w*|ноутбук\w*)',str(q),re.I)) or command_question(q) or bool(re.search(r'\b(?:как\s+(?:перезагруз|перезапуст|установ|настро)\w*)',str(q),re.I))


def platform_ok(q,text):
    qn,tn=_normal(q),_normal(text)
    if 'windows' in qn and re.search(r'\b(?:linux|линукс\w*|apt-get|systemctl)\b|/home/',tn) and 'windows' not in tn:return False
    if re.search(r'\b(?:linux|линукс\w*)',qn) and 'windows' in tn and not re.search(r'\b(?:linux|линукс\w*)',tn):return False
    return True


def relevant(q,text):
    if not platform_ok(q,text):return False
    qt=topics(q);tt=topics(text)
    return bool(qt & tt) if qt else False


# A structural test only. It never maps a question to a particular command.
_COMMAND=re.compile(r'(?:`{1,3}[^`\n]*\b[a-z][\w.-]*(?:\s+[^`\n]+)?`{1,3}|\b[a-z][\w.-]*(?:\.exe)?\s+(?:/[-\w:]+|--?[\w-]+)(?:\s+[^\n]{0,160})?)',re.I)

def has_command(text):return bool(_COMMAND.search(str(text)))


def clean(q,text):
    """Keep answer content; technical composition/check logs belong in trace."""
    text=str(text or '')
    if not practical(q):return text.strip()
    blocks=re.split(r'\n\s*\n',text)
    kept=[]
    for block in blocks:
        if re.match(r'^\s*[_*]*(?:Составлено:|Своя LLM от себя|мозг[^\n]{0,50} от себя|Своя LLM сверилась|Проверка в интернете)',block,re.I):continue
        if re.match(r'^\s*[?✓≠]\s*(?:подтверж|источник)',block):continue
        lines=[line for line in block.splitlines() if not re.search(r'Составлено:|подтверждения не нашла:|Своя LLM сверилась',line,re.I)]
        if lines:
            content='\n'.join(lines)
            focused=[]
            for fragment in re.split(r'(```[^\n]*\n[\s\S]*?```)',content):
                if fragment.startswith('```'):
                    if has_command(fragment) and platform_ok(q,fragment):focused.append(fragment)
                    continue
                for line in re.split(r'\n|(?<=[.!?])\s+(?=[А-ЯA-Z])',fragment):
                    if relevant(q,line) or (has_command(line) and platform_ok(q,line)):
                        focused.append(line)
                    elif (kept or focused) and re.search(r'^\s*[-•]?\s*/[-\w]+|несохран|сохраните.*(?:файл|работ|документ)',line,re.I):
                        focused.append(line)
            if focused:kept.append('\n'.join(focused))
    return '\n\n'.join(kept).strip()


def usable(q,text,*,grounded=False):
    text=clean(q,text)
    if not text or not platform_ok(q,text):return False
    if command_question(q):
        return has_command(text) and (grounded or relevant(q,text))
    if hardware(q) and specification(q):
        if re.search(r'максимальн|расшир|добавить|увеличить',str(q),re.I) and not re.search(r'максимальн|поддерж|слот|расшир|замен|модул|\bдо\s+\d',text,re.I):return False
        if re.search(r'сколько.{0,12}слот',str(q),re.I) and not re.search(r'слот|разъ[её]м|dimm',text,re.I):return False
        return relevant(q,text) and bool(re.search(r'\b\d+(?:[.,]\d+)?\s*(?:гб|мб|gb|mb|гигабайт|слот|модул)|\b(?:lp)?ddr[3456]\b|so[- ]?dimm',text,re.I))
    return relevant(q,text) if practical(q) else True


def command_from_page(q,page):
    """Extract a command in topical context from a fetched page, never invent it."""
    raw=str(page.get('text') or '')
    action=topics(q)-{'windows','linux'}
    candidates=[]
    for match in _COMMAND.finditer(raw[:80000]):
        start=max(raw.rfind('\n',0,match.start()),raw.rfind('. ',0,match.start()))+1
        end=raw.find('\n',match.end())
        context=raw[max(0,start-120):min(len(raw),match.end()+180)]
        overlap=action & topics(context)
        if not overlap or not platform_ok(q,context):continue
        quote=raw[start:min(len(raw),end if end>=0 else match.end()+120)].strip()[:650]
        if has_command(quote):candidates.append((len(overlap),-len(quote),quote))
    if not candidates:return ''
    return max(candidates)[2]


# These checks ask for missing identifiers; they never supply device specifications.
def hardware(q):
    return bool(re.search(r'\b(?:моноблок\w*|ноутбук\w*|оперативн\w*|озу|ram|ddr[3456]?|sodimm|so-dimm)\b',str(q),re.I))


def specification(q):
    return bool(re.search(r'сколько|какая|какой|объ[её]м|максимальн|расшир|добавить|увеличить|совместим|под[оё]йд[её]т|слот|тип\s+памяти',str(q),re.I))


def model_ids(q):
    tokens=re.findall(r'\b[a-z0-9][a-z0-9_-]*\b',str(q).lower())
    return [t for t in tokens if ((re.search(r'[a-z]',t) and re.search(r'\d',t) and not re.match(r'(?:lp)?ddr|sodimm',t) and not re.search(r'20\d\d$',t)) or (t.isdigit() and len(t)>=3 and not re.fullmatch(r'(?:19|20)\d\d',t)))]


def clarification(q,ctx=None):
    if re.search(r'как\s+(?:проверить|посмотреть|узнать)|диспетчер\s+задач|free\s+-|нехватк|тормоз|занят[ао]?\s+памят',str(q),re.I):return ''
    short_device=bool(re.search(r'моноблок|ноутбук',str(q),re.I)) and len(str(q).split())<=6
    context=' '.join(str(m.get('text') or m.get('content') or '') for m in (ctx or [])[-4:] if m.get('role')=='user')
    needs=specification(q) or (short_device and (specification(context) or re.search(r'20\d\d',str(q))))
    device_scope=bool(re.search(r'моноблок|ноутбук|компьютер|\bпк\b',str(q),re.I))
    if not device_scope or not hardware(q) or not needs or model_ids(q):return ''
    return ('Для точных характеристик нужна полная модель устройства с наклейки на корпусе. '
            'Пришли её, пожалуйста: по производителю и году нельзя определить объём, тип и возможность расширения памяти.')


def sources_for(q,sources):
    from urllib.parse import urlsplit
    identifiers=model_ids(q) if hardware(q) else []
    out=[];seen=set()
    for row in sources or []:
        if not isinstance(row,dict):continue
        url=str(row.get('url') or '')
        try:valid=urlsplit(url).scheme in ('https','http') and bool(urlsplit(url).hostname)
        except ValueError:valid=False
        if not valid or url in seen:continue
        evidence=' '.join(str(row.get(k) or '') for k in ('title','snippet','text','description')).lower()
        if identifiers and not all(re.search(r'(?<![a-z0-9])'+re.escape(t)+r'(?![a-z0-9])',evidence) for t in identifiers):continue
        seen.add(url);out.append(row)
    return out


def source_excerpt(q,sources):
    for source in sources_for(q,sources):
        evidence=str(source.get('snippet') or source.get('text') or source.get('description') or '')
        candidate=clean(q,evidence[:4000])
        if usable(q,candidate,grounded=True):return candidate[:1500],[source]
    return '',[]


def context_question(q,ctx=None):
    """Resolve a short model identifier only against an explicit recent device question."""
    q=str(q)
    if hardware(q) or len(q.split())>8 or not model_ids(q):return q
    for m in reversed((ctx or [])[-6:]):
        previous=str(m.get('text') or m.get('content') or '')
        if m.get('role')=='user' and previous!=q and hardware(previous) and specification(previous):
            return previous+'\nУточнённая модель: '+q
    return q
