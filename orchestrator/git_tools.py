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
        except ValueError: raise GitToolError('GIT_PATH_OUTSIDE_ROOT',value) from None
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

def _split_porcelain(raw):
    """Split git's `-z` porcelain into branch headers + one STRING PER RECORD.

    B021: in `-z` mode a rename/copy record (type 2) is TWO NUL-separated
    fields -- the record, then the original path -- because a path cannot carry
    the tab that separates them in the non-`-z` format. git-status(1) documents
    this. The old splitter treated that second field as a record of its own, so
    every rename produced a PHANTOM record whose `record_type` was the first
    character of the old path and whose only other content was `raw`:

        {'record_type': 'o', 'raw': 'old.txt'}          # from `old.txt`

    That phantom was counted in `counts['changed']`, and when the old path began
    with `u`, `1`, `2` or `!` it was parsed as an unmerged / ordinary / ignored
    record instead -- `rec.split(' ',10)[10]` then raised IndexError. Measured:
    `git mv util.py helper.py` made git_status RAISE, which takes git_stage and
    git_commit down with it because both call status() first. `orig_path` was
    always '' for the same reason: its value had been split off into the phantom.

    The two fields are rejoined with a TAB -- exactly the shape the non-`-z`
    format uses, and the shape the type-2 parser already expected.
    """
    fields=[f for f in raw.split('\x00') if f]
    branch={}; body=[]; index=0
    while index<len(fields):
        field=fields[index]; index+=1
        if field.startswith('# '):
            key,_,value=field[2:].partition(' '); branch[key]=value; continue
        if field.startswith('2 ') and index<len(fields):
            field=field+'\t'+fields[index]; index+=1
        body.append(field)
    return branch,body

def _parse_records(lines):
    """Parse porcelain records into fields, WITHOUT keeping the line they came from.

    B021: each record used to carry `raw` -- the porcelain line -- next to
    record_type/xy/sub/path/orig_path, which are the fields parsed OUT of it.
    Nothing in `raw` was new information and it was ~71% of the record's bytes,
    on the one unbounded part of the payload: 270 records came to 126,770 bytes
    and the transport cut the response. `raw` is dropped from the RESPONSE only;
    _porcelain still hashes it (see there).

    A record whose shape this parser does not recognise keeps its own line under
    `unparsed` instead of raising: a future porcelain change must not be able to
    take git_status -- and with it git_stage and git_commit -- offline, and the
    line is the only place that record's data would then live.
    """
    records=[]
    for rec in lines:
        typ=rec[0]; item={'record_type':typ}
        try:
            if typ=='1':
                bits=rec.split(' ',8); item.update(xy=bits[1],sub=bits[2],path=bits[8])
            elif typ=='2':
                bits=rec.split(' ',9); item.update(xy=bits[1],sub=bits[2]); path,_,orig=bits[9].partition('\t'); item.update(path=path,orig_path=orig)
            elif typ=='u':
                bits=rec.split(' ',10); item.update(xy=bits[1],sub=bits[2],path=bits[10])
            elif typ in ('?','!'): item.update(xy=typ+typ,path=rec[2:])
            else: item['unparsed']=rec
        except IndexError:
            item['unparsed']=rec
        records.append(item)
    return records

def _porcelain(root, include_ignored=False):
    args=['--literal-pathspecs','status','--porcelain=v2','--branch','--renames','-z','--untracked-files=all']
    if include_ignored: args.append('--ignored=matching')
    raw=_run(root,args)['stdout']
    branch,body=_split_porcelain(raw)
    records=_parse_records(body)
    # The HASH INPUT keeps the full porcelain line even though the response no
    # longer does. state_hash is an optimistic lock callers hold BETWEEN calls --
    # git_stage refuses with GIT_STATE_STALE and git_stale_check reports
    # state_hash_mismatch by comparing it -- so hashing the tidied records would
    # shift every hash at once and make every held lock read as a concurrent
    # modification that never happened. Tidying a payload must not be a protocol
    # change.
    payload={'branch':branch,'records':[{**item,'raw':rec} for item,rec in zip(records,body,strict=True)]}
    return records,hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def _staged_hash(root):
    raw=_run(root,['ls-files','--stage','-z'])['stdout']
    return hashlib.sha256(raw.encode()).hexdigest()

def status(project_path, include_ignored=False, max_records=0, compact_untracked=False):
    root=resolve_root(project_path); records,state_hash=_porcelain(root,include_ignored)
    b=_run(root,['status','--porcelain=v2','--branch','--untracked-files=no'])['stdout']; ahead=behind=0
    m=re.search(r'^# branch\.ab \+(\d+) -(\d+)$',b,re.M)
    if m: ahead,behind=map(int,m.groups())
    head=_run(root,['rev-parse','--verify','HEAD'],check=False); branch=_run(root,['symbolic-ref','--short','-q','HEAD'],check=False)['stdout'].strip()
    counts={'changed':len(records),'staged':sum(1 for x in records if x.get('xy','.')[0] not in ('.',' ','?','!')),'unstaged':sum(1 for x in records if len(x.get('xy',''))>1 and x.get('xy')[1] not in ('.',' ','?','!')),'untracked':sum(1 for x in records if x.get('record_type')=='?'),'conflicts':sum(1 for x in records if x.get('record_type')=='u')}
    # records is capped because it is 89% of this payload (measured: ~216 chars per
    # record) and the ONLY unbounded part. A repo mid-refactor produced a response
    # past the transport's ~63k ceiling, so it arrived TRUNCATED -- and the field a
    # caller most needs, staged_hash, used to sit at the END of the JSON. That is the
    # whole reported failure: git_commit could not be given a hash nobody could read.
    #
    # Two independent halves of the fix, both needed:
    #  * the hashes/counts move to the FRONT, so even a truncated response still
    #    carries the optimistic lock;
    #  * records is capped, and the cap is REPORTED (records_total /
    #    records_truncated) rather than silent.
    # counts/state_hash/staged_hash are computed over EVERY record, so they stay
    # exact no matter what was dropped. A caller wanting the full list asks with
    # max_records, or reads paths from git_diff. 0 = uncapped, which keeps the old
    # behaviour for the in-process callers that walk records.
    total=len(records)
    display_records=records
    if compact_untracked:
        # Presentation only: preserve all-record counts/hashes for optimistic locks.
        display_records=[]
        groups={}
        for record in records:
            path=str(record.get('path') or '').replace('\\','/')
            if record.get('record_type')=='?' and '/' in path:
                prefix=path.split('/',1)[0]+'/'
                if prefix not in groups:
                    groups[prefix]={'record_type':'?','xy':'??','path':prefix,
                                    'aggregated':True,'untracked_count':0}
                    display_records.append(groups[prefix])
                groups[prefix]['untracked_count']+=1
            else:
                display_records.append(record)
    cap=max(0,int(max_records or 0))
    shown=display_records if not cap else display_records[:cap]
    return {'ok':True,'git_root':str(root),'branch':branch,'detached':not bool(branch),'head':head['stdout'].strip(),'ahead':ahead,'behind':behind,'state_hash':state_hash,'staged_hash':_staged_hash(root),
            # A35 (c) -- disclose the hash DOMAIN next to the hash. Measured live:
            # staged=3 and staged=0 answered the SAME staged_hash, which reads as a
            # bug and is not one. Projectable away with fields= when not needed.
            'staged_hash_semantics':('digest of the WHOLE index (git ls-files --stage), NOT the diff vs '
                                     'HEAD: a commit leaves the index byte-identical, so an unchanged '
                                     'staged_hash does NOT prove a commit is still pending. Use head + '
                                     'counts.staged to tell pending from already-committed.'),
            'counts':counts,'clean':not records,'records_total':total,'records_truncated':bool(cap and len(display_records)>cap),'records_grouped':bool(compact_untracked),'display_records_total':len(display_records),'records':shown}

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
    # 'before' used to be a FULL second status, which doubled the response (measured
    # 9341 vs 4626 chars on this repo) and carried nothing new -- the caller had just
    # read that status to obtain expected_state_hash. Only the hashes and counts are
    # kept, which is what a caller can actually act on: proof of which state it
    # staged from.
    out={'ok':True,'paths':specs,'paths_file':str(paths_file or ''),'allow_all':bool(allow_all),'before':{'state_hash':before['state_hash'],'staged_hash':before['staged_hash'],'counts':before['counts']},'after':status(str(root))}
    # A30-B: hint-only, zero token. Verified BEFORE writing this line -- ocr_review with
    # no refs returns mode "workspace" and reads the UNCOMMITTED tree (exit 0, 0.19s,
    # 15 files). So the hint names a call that really works on content that is staged but
    # not yet committed; a hint pointing at a failing call is worse than no hint at all.
    out['ca_hint_review']='ocr_review(project_path=...) reviews the uncommitted working tree before you commit; mode "workspace", delegate, 0 token'
    return out

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
    # Three DISTINCT rejections, previously collapsed into one GIT_STATE_STALE that
    # named the wrong cause. A caller that passed a hash matching the live index was
    # told the "staged content changed" -- it had not -- and the only way forward was
    # to copy current_staged_hash out of the error and resend. Reported from live use.
    #
    # "auto" is accepted as an explicit opt-out of the optimistic lock. It is NOT a
    # weakening: the guard exists so a hash read BEFORE some other actor touched the
    # index cannot commit a surprise, and a caller who never read one has nothing
    # stale to carry. Refusing it only pushed callers to the same place via one
    # wasted round-trip, while making a genuine mismatch indistinguishable from a
    # missing field. Note the read is a fresh status() taken above, in-process.
    checked=str(expected_staged_hash or '').strip()
    if not checked:
        raise GitToolError('EXPECTED_STAGED_HASH_REQUIRED',
                           'expected_staged_hash is required: pass git_status.staged_hash, '
                           'or "auto" to commit whatever is staged right now',
                           current_staged_hash=s['staged_hash'])
    if checked.lower()!='auto' and checked!=s['staged_hash']:
        raise GitToolError('GIT_STATE_STALE','staged content changed since expected_staged_hash was read',
                           current_staged_hash=s['staged_hash'],expected_staged_hash=checked,
                           ca_next_action='re-read git_status and resend, or pass "auto" to accept the current index',
                           retryable=True)
    if s['counts']['staged']==0 and not allow_empty:
        # A35 (c) -- staged_hash digests the WHOLE index (`git ls-files --stage -z`),
        # not the staged diff vs HEAD. A commit does not change index CONTENT (same
        # blobs, same modes) -- it only moves HEAD -- so the digest is byte-identical
        # before and after, and the optimistic lock above PASSES on a retry that has
        # nothing left to commit. Measured live: staged=3 and staged=0 answered the
        # same staged_hash. That is correct by design (changing the recipe would make
        # every held lock read as a concurrent modification, and would break the
        # KIND_COMMIT advisory cache key below), so the fix is DISCLOSURE: name HEAD
        # and the two fields that DO discriminate, instead of letting a client-timeout
        # retry read as state confusion.
        _head=_run(root,['rev-parse','HEAD'],check=False)['stdout'].strip()
        _subject=_run(root,['log','-1','--pretty=%s'],check=False)['stdout'].strip()
        raise GitToolError('NOTHING_STAGED','nothing staged to commit',
                           head=_head or 'unknown', head_subject=_subject,
                           staged_hash=s['staged_hash'], state_hash=s['state_hash'],
                           staged_count=0,
                           why=('expected_staged_hash matching does NOT mean a commit is still '
                                'pending: staged_hash digests the whole index, which a commit '
                                'leaves byte-identical. If an earlier commit call client-timed-out, '
                                'THIS error is the expected answer to the retry -- it already landed.'),
                           ca_next_action=('compare head_subject with the message you sent: same subject '
                                           '= already committed, stop retrying. Otherwise stage with '
                                           'git_add, or pass allow_empty=True for an empty commit.'))
    for key in ('user.name','user.email'):
        if not _run(root,['config','--get',key],check=False)['stdout'].strip(): raise GitToolError('GIT_IDENTITY_MISSING',f'{key} is not configured')
    args=['commit','-m',msg]
    if allow_empty: args.append('--allow-empty')
    if no_verify: args.append('--no-verify')
    committed_paths=[str(r.get('path')) for r in s.get('records',[]) if str(r.get('xy','.'))[:1] not in ('.',' ','?','!') and r.get('path')]
    res=_run(root,args,timeout=300); head=_run(root,['rev-parse','HEAD'])['stdout'].strip()
    out={'ok':True,'commit':head,'stdout':res['stdout'],'stderr':res['stderr'],'status':status(str(root)),'committed_files':committed_paths}
    # A30-B: hint-only, zero token. OCR consumes a commit range, which is exactly what
    # just came into existence -- verified live on c25bb81 (exit 0, 10 reviewable of 13).
    # This attaches a STRING, never a subprocess: a commit must not pay for a review the
    # caller did not ask for.
    out['ca_hint_review']=f'ocr_review(project_path=..., commit="{head}") reviews this commit semantically; delegate mode, 0 token'
    # P7-S3 H3: advisory AFTER successful commit+hash, BEFORE return.
    # Flag-OFF = no hooks/graph/status/parser import. Throw cannot fail the commit.
    if os.environ.get("CBM_ENRICH_COMMIT", "0").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from orchestrator.graph.hooks import KIND_COMMIT, lookup_enrich, scoped_fingerprint
            staged_raw = checked if checked.lower() != "auto" else str(s.get("staged_hash") or "")
            staged_key = scoped_fingerprint(str(root), staged_raw) if staged_raw else ""
            if not staged_key or checked.lower() == "auto":
                out["advisory"] = {"status": "unavailable", "reason": "partial_stage", "payload": None}
            else:
                # GAP-01/H3: warm KIND_COMMIT keyed by the PRE-commit staged_hash
                # so the lookup below hits a real advisory instead of cache_miss.
                # Staged paths come from the index metadata already in s (captured
                # before the commit ran), split prod/test by PATH NAME -- never
                # stdout. TTL is 60s, so this must run at commit time, which it
                # does (inside commit(), after the staged key is known).
                _staged_paths = [
                    str(r.get("path")) for r in (s.get("records") or [])
                    if str(r.get("xy", "."))[:1] not in (".", " ", "?", "!") and r.get("path")
                ]
                if _staged_paths:
                    try:
                        from orchestrator.graph.enrich_producers import produce_commit_advisory as _cbm_warm_commit
                        _warm = _cbm_warm_commit(str(root), staged_raw, _staged_paths)
                        if isinstance(_warm, dict):
                            out["advisory_warm"] = {"warmed": bool(_warm.get("warmed")),
                                                    "reason": _warm.get("reason")}
                    except Exception:
                        pass  # fail-open: a warm miss leaves the honest cache_miss path
                hit = lookup_enrich(KIND_COMMIT, staged_key)
                if hit.get("status") == "ok":
                    out["advisory"] = {"status": "ok", "reason": None, "payload": hit.get("payload")}
                else:
                    reason = str(hit.get("reason") or "cache_miss")
                    if reason in ("cache_miss", "cache_stale", "fingerprint_mismatch"):
                        reason = "unavailable" if reason == "fingerprint_mismatch" else reason
                    out["advisory"] = {"status": "unavailable", "reason": reason, "payload": None}
        except Exception:
            out["advisory"] = {"status": "unavailable", "reason": "advisory_error", "payload": None}
    return out

def log(project_path, limit=20, ref='HEAD'):
    root=resolve_root(project_path); n=max(1,min(int(limit),200)); raw=_run(root,['log',f'-{n}','--date=iso-strict','--pretty=format:%H%x1f%h%x1f%an%x1f%ae%x1f%ad%x1f%s',str(ref)])['stdout']; rows=[]
    for line in raw.splitlines():
        bits=line.split('\x1f')
        if len(bits)==6: rows.append(dict(zip(['hash','short_hash','author','email','date','subject'],bits,strict=True)))
    return {'ok':True,'commits':rows,'ref':ref}

def safe_call(fn,*args,**kwargs):
    try: return fn(*args,**kwargs)
    except GitToolError as e: return {'ok':False,'error_name':e.error_name,'error':str(e),**e.extra}
