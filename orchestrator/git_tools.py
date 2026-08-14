"""Governed Git porcelain helpers for ToolA."""
from __future__ import annotations
import fnmatch, hashlib, json, os, re, subprocess, tempfile, time
from pathlib import Path

class GitToolError(RuntimeError):
    def __init__(self, name, message, **extra):
        self.error_name=name; self.extra=extra; super().__init__(message)

def _run(root, args, timeout=60, check=True):
    env=os.environ.copy(); env.update({'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never','LC_ALL':'C','LANG':'C'})
    try:
        p=subprocess.run(['git','-C',str(root),*args],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',timeout=max(1,min(int(timeout),300)),env=env,shell=False)
    except FileNotFoundError as e: raise GitToolError('GIT_NOT_INSTALLED','git executable not found') from e
    except subprocess.TimeoutExpired as e: raise GitToolError('GIT_TIMEOUT',f'git command timed out after {timeout}s') from e
    out={'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    if check and p.returncode: raise GitToolError('GIT_COMMAND_FAILED',(p.stderr or p.stdout or 'git failed').strip()[:1000],exit_code=p.returncode)
    return out

def resolve_root(project_path):
    root=Path(project_path).resolve()
    if not root.is_dir(): raise GitToolError('PROJECT_PATH_NOT_FOUND',str(root))
    top=_run(root,['rev-parse','--show-toplevel'])['stdout'].strip()
    if not top: raise GitToolError('NOT_A_GIT_REPOSITORY',str(root))
    actual=Path(top).resolve()
    if os.path.normcase(str(actual)) != os.path.normcase(str(root)): raise GitToolError('GIT_ROOT_MISMATCH',f'project_path must equal git top-level: {actual}',git_root=str(actual))
    return actual

_SECRET_PATTERNS = ('.env', '.env.*', '*.pem', '*.p12', '*.pfx', '*.key', 'id_*', '*credentials*')
def _pathspecs(root, paths):
    result=[]
    for raw in paths or []:
        value=str(raw).replace('\\','/').strip()
        if not value or chr(0) in value or value.startswith('-') or value.startswith(':') or any(ch in value for ch in '*?[') or Path(value).is_absolute(): raise GitToolError('INVALID_GIT_PATHSPEC',value)
        low=value.lower()
        if low=='.git' or low.startswith('.git/') or any(fnmatch.fnmatch(Path(low).name, pat) for pat in _SECRET_PATTERNS): raise GitToolError('PATHSPEC_DENYLISTED',value)
        candidate=(root/value).resolve()
        try: candidate.relative_to(root)
        except ValueError: raise GitToolError('GIT_PATH_OUTSIDE_ROOT',value)
        result.append(value)
    return list(dict.fromkeys(result))

def _pathspecs_from_file(root, paths_file):
    if not paths_file:
        return []
    source=Path(paths_file).resolve()
    if not source.is_file():
        raise GitToolError('GIT_PATHS_FILE_NOT_FOUND',str(paths_file))
    try:
        text=source.read_text(encoding='utf-8')
    except UnicodeDecodeError as e:
        raise GitToolError('INVALID_GIT_PATHS_FILE',f'{paths_file}: expected UTF-8') from e
    if chr(0) in text:
        raise GitToolError('INVALID_GIT_PATHS_FILE',f'{paths_file}: NUL is not allowed')
    specs=[]
    for index, raw in enumerate(text.splitlines(),1):
        try:
            specs.extend(_pathspecs(root,[raw]))
        except GitToolError as e:
            raise GitToolError(e.error_name,
                               f'paths_file entry {index} rejected: {raw!r}',
                               offending_entry=raw, entry_number=index,
                               **e.extra) from e
    if not specs:
        raise GitToolError('GIT_PATHS_REQUIRED','paths_file contains no paths')
    return list(dict.fromkeys(specs))

def _run_pathspec_file(root, command, specs):
    """Feed arbitrarily large explicit batches without hitting argv limits."""
    handle=tempfile.NamedTemporaryFile(prefix='toola-git-paths-',suffix='.txt',
                                       dir=str(root),delete=False)
    path=Path(handle.name)
    try:
        with handle:
            handle.write(b''.join(value.encode('utf-8')+b'\0' for value in specs))
        return _run(root,[*command,f'--pathspec-from-file={path}','--pathspec-file-nul'])
    finally:
        path.unlink(missing_ok=True)

def _porcelain(root, include_ignored=False):
    args=['--literal-pathspecs','status','--porcelain=v2','--branch','--renames','-z','--untracked-files=all']
    if include_ignored: args.append('--ignored=matching')
    raw=_run(root,args)['stdout']; records=[]; branch={}
    for rec in raw.split('\x00'):
        if not rec: continue
        if rec.startswith('# '):
            key,_,value=rec[2:].partition(' '); branch[key]=value; continue
        typ=rec[0]; item={'record_type':typ,'raw':rec}
        if typ=='1':
            bits=rec.split(' ',8); item.update(xy=bits[1],sub=bits[2],path=bits[8])
        elif typ=='2':
            bits=rec.split(' ',9); item.update(xy=bits[1],sub=bits[2]); path,_,orig=bits[9].partition('\t'); item.update(path=path,orig_path=orig)
        elif typ=='u':
            bits=rec.split(' ',10); item.update(xy=bits[1],sub=bits[2],path=bits[10])
        elif typ in ('?','!'): item.update(xy=typ+typ,path=rec[2:])
        records.append(item)
    payload={'branch':branch,'records':records}
    return records,hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def _staged_hash(root):
    raw=_run(root,['ls-files','--stage','-z'])['stdout']
    return hashlib.sha256(raw.encode()).hexdigest()

def status(project_path, include_ignored=False):
    root=resolve_root(project_path); records,state_hash=_porcelain(root,include_ignored)
    b=_run(root,['status','--porcelain=v2','--branch','--untracked-files=no'])['stdout']; ahead=behind=0
    m=re.search(r'^# branch\.ab \+(\d+) -(\d+)$',b,re.M)
    if m: ahead,behind=map(int,m.groups())
    head=_run(root,['rev-parse','--verify','HEAD'],check=False); branch=_run(root,['symbolic-ref','--short','-q','HEAD'],check=False)['stdout'].strip()
    counts={'changed':len(records),'staged':sum(1 for x in records if x.get('xy','.')[0] not in ('.',' ','?','!')),'unstaged':sum(1 for x in records if len(x.get('xy',''))>1 and x.get('xy')[1] not in ('.',' ','?','!')),'untracked':sum(1 for x in records if x.get('record_type')=='?'),'conflicts':sum(1 for x in records if x.get('record_type')=='u')}
    return {'ok':True,'git_root':str(root),'branch':branch,'detached':not bool(branch),'head':head['stdout'].strip(),'ahead':ahead,'behind':behind,'records':records,'counts':counts,'clean':not records,'state_hash':state_hash,'staged_hash':_staged_hash(root)}

def stale_check(project_path, max_commit_age_hours=168, expected_state_hash=''):
    root=resolve_root(project_path); s=status(str(root)); git=root/'.git'; states=[n for n in ['MERGE_HEAD','CHERRY_PICK_HEAD','REVERT_HEAD','BISECT_LOG','rebase-merge','rebase-apply'] if (git/n).exists()]
    ts=_run(root,['log','-1','--format=%ct'],check=False)['stdout'].strip(); age=(time.time()-int(ts))/3600 if ts.isdigit() else None; stale=[]
    if not s['clean']: stale.append('dirty_worktree')
    if s['counts']['conflicts']: stale.append('conflicts')
    if s['detached']: stale.append('detached_head')
    if states: stale.append('operation_in_progress')
    if s['behind']>0: stale.append('behind_upstream_local_ref')
    if age is not None and age>float(max_commit_age_hours): stale.append('old_head_commit')
    changed=bool(expected_state_hash and expected_state_hash!=s['state_hash'])
    if changed: stale.append('state_hash_mismatch')
    return {'ok':True,'stale':bool(stale),'reasons':stale,'operation_states':states,'commit_age_hours':age,'expected_state_changed':changed,'status':s,'note':'ahead/behind uses local refs only; no fetch performed'}

def diff(project_path, scope='worktree', paths=None, context_lines=3, max_chars=50000, check_whitespace=False):
    root=resolve_root(project_path); specs=_pathspecs(root,paths); scope=str(scope).lower(); base=['--literal-pathspecs','diff','--no-ext-diff','--no-textconv','--no-color',f'--unified={max(0,min(int(context_lines),20))}']
    if scope not in ('worktree','staged','both'): raise GitToolError('INVALID_GIT_DIFF_SCOPE',scope)
    def get(staged=False, check=False):
        args=list(base)
        if staged: args.append('--cached')
        if check: args.append('--check')
        if specs: args+=['--',*specs]
        return _run(root,args,check=not check)
    chunks=[]
    if scope in ('worktree','both'): chunks.append(('WORKTREE',get(False)['stdout']))
    if scope in ('staged','both'): chunks.append(('STAGED',get(True)['stdout']))
    text=''.join((('=== '+name+' ==='+chr(10)) if scope=='both' else '')+patch for name,patch in chunks)
    whitespace=[]
    if check_whitespace:
        for staged in ([False,True] if scope=='both' else [scope=='staged']):
            check_result=get(staged,True)
            report=(check_result.get('stdout') or check_result.get('stderr') or '').strip()
            defects=chr(10).join(line for line in report.splitlines() if not line.lower().startswith('warning:'))
            if defects: whitespace.append(defects)
    cap=max(1000,min(int(max_chars),500000))
    return {'ok':not whitespace,'scope':scope,'diff':text[:cap],'truncated':len(text)>cap,'total_chars':len(text),'paths':specs,'whitespace_errors':whitespace,'exit_code':0 if not whitespace else 2}

def stage(project_path, paths=None, paths_file='', allow_all=False, expected_state_hash=''):
    root=resolve_root(project_path); before=status(str(root))
    if expected_state_hash and expected_state_hash!=before['state_hash']: raise GitToolError('GIT_STATE_STALE','working tree changed',current_state_hash=before['state_hash'])
    if paths and paths_file: raise GitToolError('GIT_PATH_INPUT_CONFLICT','pass paths or paths_file, not both')
    specs=_pathspecs_from_file(root,paths_file) if paths_file else _pathspecs(root,paths)
    if not specs and not allow_all: raise GitToolError('GIT_PATHS_REQUIRED','paths required unless allow_all=true')

    if allow_all: raise GitToolError('GIT_ALLOW_ALL_REFUSED','allow_all is disabled; pass explicit safe paths')
    if paths_file: _run_pathspec_file(root,['--literal-pathspecs','add'],specs)
    else: _run(root,['--literal-pathspecs','add','--',*specs])
    return {'ok':True,'paths':specs,'paths_file':str(paths_file or ''),'allow_all':bool(allow_all),'before':before,'after':status(str(root))}

def unstage(project_path, paths=None, paths_file='', allow_all=False, expected_staged_hash=''):
    root=resolve_root(project_path); before=status(str(root))
    if expected_staged_hash and expected_staged_hash!=before['staged_hash']: raise GitToolError('GIT_STATE_STALE','index changed',current_staged_hash=before['staged_hash'])
    if paths and paths_file: raise GitToolError('GIT_PATH_INPUT_CONFLICT','pass paths or paths_file, not both')
    specs=_pathspecs_from_file(root,paths_file) if paths_file else _pathspecs(root,paths)
    if not specs and not allow_all: raise GitToolError('GIT_PATHS_REQUIRED','paths required unless allow_all=true')

    if allow_all: raise GitToolError('GIT_ALLOW_ALL_REFUSED','allow_all is disabled; pass explicit paths')
    if paths_file: _run_pathspec_file(root,['--literal-pathspecs','restore','--staged'],specs)
    else: _run(root,['--literal-pathspecs','restore','--staged','--',*specs])
    return {'ok':True,'paths':specs,'paths_file':str(paths_file or ''),'after':status(str(root))}

def commit(project_path, message, expected_staged_hash, allow_empty=False, no_verify=False):
    root=resolve_root(project_path); s=status(str(root)); msg=str(message or '').strip()
    if not msg or len(msg)>4000: raise GitToolError('INVALID_COMMIT_MESSAGE','message required and max 4000 chars')
    if s['counts']['conflicts']: raise GitToolError('GIT_CONFLICTS_PRESENT','resolve conflicts before commit')
    if not expected_staged_hash or expected_staged_hash!=s['staged_hash']: raise GitToolError('GIT_STATE_STALE','staged content changed or expected_staged_hash missing',current_staged_hash=s['staged_hash'])
    if s['counts']['staged']==0 and not allow_empty: raise GitToolError('NOTHING_STAGED','nothing staged to commit')
    for key in ('user.name','user.email'):
        if not _run(root,['config','--get',key],check=False)['stdout'].strip(): raise GitToolError('GIT_IDENTITY_MISSING',f'{key} is not configured')
    args=['commit','-m',msg]
    if allow_empty: args.append('--allow-empty')
    if no_verify: args.append('--no-verify')
    res=_run(root,args,timeout=300); head=_run(root,['rev-parse','HEAD'])['stdout'].strip()
    return {'ok':True,'commit':head,'stdout':res['stdout'],'stderr':res['stderr'],'status':status(str(root))}

def log(project_path, limit=20, ref='HEAD'):
    root=resolve_root(project_path); n=max(1,min(int(limit),200)); raw=_run(root,['log',f'-{n}','--date=iso-strict','--pretty=format:%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s',str(ref)])['stdout']; rows=[]
    for line in raw.splitlines():
        bits=line.split('\x1f')
        if len(bits)==6: rows.append(dict(zip(['hash','short_hash','author','email','date','subject'],bits)))
    return {'ok':True,'commits':rows,'ref':ref}

def safe_call(fn,*args,**kwargs):
    try: return fn(*args,**kwargs)
    except GitToolError as e: return {'ok':False,'error_name':e.error_name,'error':str(e),**e.extra}
