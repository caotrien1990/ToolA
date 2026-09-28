import tempfile, subprocess
from pathlib import Path
from unittest import mock
from orchestrator import git_tools as g

def run(root,*args): return subprocess.run(['git','-C',str(root),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,text=True)
def repo():
 d=tempfile.TemporaryDirectory(ignore_cleanup_errors=True); root=Path(d.name); run(root,'init');run(root,'config','user.name','Test');run(root,'config','user.email','test@example.com');(root/'a.txt').write_text('one\n');run(root,'add','a.txt');run(root,'commit','-m','init');return d,root

def test_status_diff_stale_and_log():
 d,r=repo();(r/'a.txt').write_text('two\n');s=g.status(str(r));assert not s['clean'] and s['state_hash'];assert 'two' in g.diff(str(r))['diff'];assert 'dirty_worktree' in g.stale_check(str(r))['reasons'];assert g.log(str(r))['commits'][0]['subject']=='init';d.cleanup()
def test_stage_stale_unstage_and_commit():
 d,r=repo();(r/'b.txt').write_text('b');before=g.status(str(r));st=g.stage(str(r),['b.txt'],expected_state_hash=before['state_hash']);assert st['after']['counts']['staged']==1;bad=g.safe_call(g.commit,str(r),'x','bad');assert bad['error_name']=='GIT_STATE_STALE';u=g.unstage(str(r),['b.txt'],expected_staged_hash=st['after']['staged_hash']);assert u['after']['counts']['staged']==0;st=g.stage(str(r),['b.txt']);c=g.commit(str(r),'add b',st['after']['staged_hash']);assert c['ok'];d.cleanup()
def test_path_guards_and_root_mismatch():
 d,r=repo();(r/'sub').mkdir();assert g.safe_call(g.status,str(r/'sub'))['error_name']=='GIT_ROOT_MISMATCH';assert g.safe_call(g.stage,str(r),['../x'])['error_name']=='GIT_PATH_OUTSIDE_ROOT';d.cleanup()


def test_pathspec_magic_secrets_and_allow_all_refused():
 d,r=repo(); (r/'.env').write_text('secret');
 assert g.safe_call(g.stage,str(r),[':(glob)**'])['error_name']=='INVALID_GIT_PATHSPEC'
 assert g.safe_call(g.stage,str(r),['*.txt'])['error_name']=='INVALID_GIT_PATHSPEC'
 assert g.safe_call(g.stage,str(r),['.env'])['error_name']=='PATHSPEC_DENYLISTED'
 assert g.safe_call(g.stage,str(r),allow_all=True)['error_name']=='GIT_ALLOW_ALL_REFUSED'; d.cleanup()

def test_diff_patch_preserved_with_whitespace_check():
 d,r=repo(); (r/'a.txt').write_text('two  '+chr(10)); result=g.diff(str(r),check_whitespace=True)
 assert 'two' in result['diff'] and result['whitespace_errors'] and not result['ok']; d.cleanup()

def test_status_staged_count_and_staged_hash_are_read_only():
 d,r=repo(); (r/'b.txt').write_text('b'); before=g.status(str(r)); g.stage(str(r),['b.txt']); after=g.status(str(r))
 assert before['counts']['staged']==0 and after['counts']['staged']==1 and after['staged_hash']; d.cleanup()


def test_commit_reports_committed_files():
 d,r=repo();(r/'b.txt').write_text('b');st=g.stage(str(r),['b.txt']);c=g.commit(str(r),'add b',st['after']['staged_hash']);assert c['committed_files']==['b.txt'];d.cleanup()

def test_paths_file_stages_and_unstages_listed_files():
 d,r=repo(); (r/'b.txt').write_text('b'); (r/'c.txt').write_text('c')
 path_file=r/'batch.paths'; path_file.write_text('b.txt\nc.txt\n',encoding='utf-8')
 staged=g.stage(str(r),paths_file=str(path_file)); assert staged['after']['counts']['staged']==2
 unstaged=g.unstage(str(r),paths_file=str(path_file),expected_staged_hash=staged['after']['staged_hash'])
 assert unstaged['after']['counts']['staged']==0; d.cleanup()

def test_paths_file_rejection_names_exact_offending_entry():
 d,r=repo(); path_file=r/'bad.paths'; path_file.write_text('a.txt\n../outside.txt\n',encoding='utf-8')
 result=g.safe_call(g.stage,str(r),paths_file=str(path_file))
 assert result['error_name']=='GIT_PATH_OUTSIDE_ROOT'
 assert result['offending_entry']=='../outside.txt' and '../outside.txt' in result['error']; d.cleanup()


def test_run_failure_keeps_the_reason_behind_long_hook_noise():
 # #27: git relays hook output on stderr BEFORE its own reason and some reasons arrive on
 # stdout, so the old head-only `(stderr or stdout)[:1000]` answered the noise alone.
 noise='hook: '+'n'*3000+'\nREAL-REASON: subject rejected'
 done=subprocess.CompletedProcess(['git'],1,stdout='nothing added to commit\n',stderr=noise)
 with mock.patch.object(g.subprocess,'run',return_value=done):
  try: g._run('.',['commit','-m','x']); raise AssertionError('a failing git call did not raise')
  except g.GitToolError as e: text=str(e); name=e.error_name
 assert name=='GIT_COMMAND_FAILED' and len(text)<=1000 and text.startswith('hook: ')
 assert 'REAL-REASON: subject rejected' in text and 'nothing added to commit' in text

def test_a_paths_file_entry_ends_at_its_newline_only():
 # U+2028 is legal in a file name (NTFS and POSIX). str.splitlines() cut the entry in two
 # and staged BOTH halves -- two other files -- and never the one listed.
 d,r=repo(); odd='x'+chr(0x2028)+'b.txt'
 for name in ('x','b.txt',odd): (r/name).write_text('n',encoding='utf-8')
 path_file=r/'batch.paths'; path_file.write_bytes((odd+'\n').encode('utf-8'))
 assert g.stage(str(r),paths_file=str(path_file))['paths']==[odd]
 staged=[x['path'] for x in g.status(str(r))['records'] if x.get('xy','.')[0] not in ('.',' ','?','!')]
 assert staged==[odd], staged; d.cleanup()

def test_log_keeps_a_subject_holding_a_unicode_line_break():
 # git ends a --pretty=format: row at "\n" only; splitlines() also broke at FF, U+2028 and
 # U+0085, so the subject came back cut at the first one.
 d,r=repo(); subject='a'+chr(0x0c)+'b'+chr(0x2028)+'c'+chr(0x85)+'d'
 msg=r.parent/(r.name+'.msg'); msg.write_bytes(subject.encode('utf-8'))
 try: run(r,'commit','-q','--allow-empty','--cleanup=verbatim','-F',str(msg))
 finally: msg.unlink(missing_ok=True)
 commits=g.log(str(r))['commits']
 assert [c['subject'] for c in commits]==[subject,'init'], commits; d.cleanup()


def test_commit_message_limit_sits_exactly_at_the_published_constant():
 # git_commit publishes maxLength from COMMIT_MESSAGE_MAX_CHARS, so the refusal must sit
 # exactly there: n characters commit, n+1 answer INVALID_COMMIT_MESSAGE.
 d,r=repo(); n=g.COMMIT_MESSAGE_MAX_CHARS; (r/'b.txt').write_text('b'); g.stage(str(r),['b.txt'])
 over=g.safe_call(g.commit,str(r),'x'*(n+1),'auto'); assert over['error_name']=='INVALID_COMMIT_MESSAGE'
 assert g.commit(str(r),'x'*n,'auto')['ok']; d.cleanup()

# --- TRAP-A / TRAP-B (measured 2026-09-28, commit 9928cbd): EOL churn ---
# SKILL.md had a uniformly-CRLF HEAD blob (221 CRLF / 0 lone LF); git_stage's plain
# `git add` under core.autocrlf=true (SYSTEM scope on this host) replaced it with an LF
# index blob, so a 37-line content change showed 443 diff lines and git_commit landed it.
# Every fixture below pins core.autocrlf=true LOCALLY so these tests do not depend on
# the host's system gitconfig being present.
# Comment anchors name FUNCTIONS + guarded EXPRESSIONS, never line numbers: the fix
# lines shift on every edit and nothing checks them.

def crlf_repo():
 # Repo whose f.md HEAD blob is uniformly CRLF, built the way the trap file was:
 # exact bytes via hash-object --no-filters + update-index, then commit.
 d=tempfile.TemporaryDirectory(ignore_cleanup_errors=True); root=Path(d.name)
 run(root,'init'); run(root,'config','core.autocrlf','true')
 run(root,'config','user.name','Test'); run(root,'config','user.email','test@example.com')
 (root/'f.md').write_bytes(b'line one\r\nline two\r\nline three\r\nline four\r\nline five\r\n')
 sha=run(root,'hash-object','-w','--no-filters','--','f.md').stdout.strip()
 run(root,'update-index','--add','--cacheinfo','100644',sha,'f.md')
 run(root,'commit','-m','crlf head blob')
 return d,root

def mixed_repo():
 # Repo whose f.md HEAD blob is MIXED (CRLF lines AND lone-LF lines), built the same
 # exact-bytes way as crlf_repo: hash-object --no-filters + update-index + commit.
 d=tempfile.TemporaryDirectory(ignore_cleanup_errors=True); root=Path(d.name)
 run(root,'init'); run(root,'config','core.autocrlf','true')
 run(root,'config','user.name','Test'); run(root,'config','user.email','test@example.com')
 (root/'f.md').write_bytes(b'line one\r\nline two\nline three\r\nline four\nline five\r\n')
 sha=run(root,'hash-object','-w','--no-filters','--','f.md').stdout.strip()
 run(root,'update-index','--add','--cacheinfo','100644',sha,'f.md')
 run(root,'commit','-m','mixed head blob')
 return d,root

def blob_bytes(root,ref):
 return subprocess.run(['git','-C',str(root),'cat-file','blob',ref],check=True,stdout=subprocess.PIPE,stdin=subprocess.DEVNULL).stdout

def numstat(root,*extra):
 parts=run(root,'diff','--cached','--numstat','--no-renames',*extra).stdout.strip().split('\t')
 return (parts[0],parts[1])

def is_uniform_crlf(data):
 return data.count(b'\n')>0 and data.count(b'\r\n')==data.count(b'\n')

def test_stage_keeps_a_uniform_crlf_head_blob_crlf():
 # Pins stage()'s TRAP-A loop (or spec in specs: if _head_blob_is_uniform_crlf(...)
 # : _stage_crlf_preserving(...)`) and _stage_crlf_preserving's pipeline: the LF->CRLF
 # normalization `data.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')`, `hash-object -w
 # --no-filters --stdin` and the three-arg `update-index --cacheinfo`. CRLF HEAD blob +
 # LF worktree with ONE changed line: the staged blob must stay uniform CRLF and the
 # raw numstat must equal the --ignore-cr-at-eol numstat (1/1, not 5/5).
 d,r=crlf_repo()
 (r/'f.md').write_bytes(b'line one\nline TWO CHANGED\nline three\nline four\nline five\n')
 st=g.stage(str(r),['f.md'])
 assert st['ok'] and st['after']['counts']['staged']==1
 idx_sha=run(r,'ls-files','--stage','--','f.md').stdout.split()[1]
 assert is_uniform_crlf(blob_bytes(r,idx_sha)),'staged blob lost the HEAD CRLF convention'
 assert numstat(r)==('1','1') and numstat(r,'--ignore-cr-at-eol')==('1','1')
 d.cleanup()

def test_stage_crlf_preserving_leaves_no_file_in_the_worktree():
 # Pins _stage_crlf_preserving's STDIN transport (`_run_bytes(...,input=norm)` ->
 # `hash-object -w --no-filters --stdin`): the earlier NamedTemporaryFile(dir=root)
 # recipe could leave a `toola-git-crlf-*` orphan in the repo on a kill mid-stage,
 # visible to the indexer/watchers. After a CRLF-preserving stage the worktree must
 # hold NO new untracked file of any kind.
 d,r=crlf_repo()
 (r/'f.md').write_bytes(b'line one\nline TWO CHANGED\nline three\nline four\nline five\n')
 st=g.stage(str(r),['f.md'])
 assert st['ok'] and is_uniform_crlf(blob_bytes(r,run(r,'ls-files','--stage','--','f.md').stdout.split()[1]))
 untracked=[x['path'] for x in g.status(str(r))['records'] if x.get('record_type')=='?']
 assert untracked==[],f'stage left untracked residue: {untracked}'
 assert not list(r.glob('toola-git-crlf-*')),'a stdin-hash stage must not touch the repo root'
 d.cleanup()

def test_stage_lf_head_blob_keeps_plain_git_add_behavior():
 # Pins _head_blob_is_uniform_crlf's uniformity expression (`n>0 and
 # data.count(b'\r\n')==n) answering False for an LF HEAD blob, so stage() never
 # reaches _stage_crlf_preserving: with autocrlf=true a CRLF worktree file still
 # stages as an LF blob, exactly like before.
 d,r=repo(); run(r,'config','core.autocrlf','true')
 (r/'g.md').write_bytes(b'x\r\ny\r\n'); g.stage(str(r),['g.md']); g.commit(str(r),'add g','auto')
 assert not is_uniform_crlf(blob_bytes(r,'HEAD:g.md')),'fixture must have an LF HEAD blob'
 (r/'g.md').write_bytes(b'x\r\nz\r\n')
 g.stage(str(r),['g.md'])
 idx_sha=run(r,'ls-files','--stage','--','g.md').stdout.split()[1]
 assert blob_bytes(r,idx_sha)==b'x\nz\n','an LF HEAD blob must keep staging as LF'
 d.cleanup()

def test_stage_new_file_keeps_plain_git_add_behavior():
 # Pins _head_blob_is_uniform_crlf's `if p.returncode: return False`: a path with NO
 # HEAD blob (new file) keeps the plain `git add` result byte-for-byte.
 d,r=repo(); run(r,'config','core.autocrlf','true')
 (r/'new.md').write_bytes(b'a\r\nb\r\n'); g.stage(str(r),['new.md'])
 idx_sha=run(r,'ls-files','--stage','--','new.md').stdout.split()[1]
 assert blob_bytes(r,idx_sha)==b'a\nb\n','a new file must keep the plain git add normalization'
 d.cleanup()

def test_stage_lone_cr_worktree_fails_closed_to_plain_git_add():
 # Pins _stage_crlf_preserving's fail-closed guard (`if b'\r' in
 # data.replace(b'\r\n',b''): return False`): a lone CR must NOT be rewritten into a
 # line break -- the index would then differ from the worktree in CONTENT, not only
 # in EOL. This worktree holds LF lines AND a lone CR, and plain `git add` (measured
 # on this host, git 2.53.0) stages such bytes VERBATIM -- so a guard-free normalize
 # would produce an all-CRLF blob that DIFFERS from the sibling's plain-add blob,
 # and only the guarded code keeps the two byte-identical.
 d,r=crlf_repo()
 wt=b'line one\nline TWO\rCHANGED\nline three\nline four\nline five\n'
 (r/'f.md').write_bytes(wt)
 g.stage(str(r),['f.md'])
 idx_sha=run(r,'ls-files','--stage','--','f.md').stdout.split()[1]
 assert blob_bytes(r,idx_sha)!=wt.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'),'the lone CR was rewritten: the fail-closed guard did not run'
 # sibling: the SAME repo state, plain `git add` only -- the reference blob
 d2,r2=crlf_repo(); (r2/'f.md').write_bytes(wt)
 run(r2,'--literal-pathspecs','add','--','f.md')
 ref_sha=run(r2,'ls-files','--stage','--','f.md').stdout.split()[1]
 assert idx_sha==ref_sha,'lone-CR worktree must keep the plain git add blob'
 assert blob_bytes(r,idx_sha)==blob_bytes(r2,ref_sha)
 d.cleanup(); d2.cleanup()

def test_stage_mixed_head_blob_keeps_plain_git_add_behavior():
 # Pins _head_blob_is_uniform_crlf's uniformity expression (`n>0 and
 # data.count(b'\r\n')==n`) against a weakened `data.count(b'\r\n')>0`: a MIXED HEAD
 # blob (CRLF lines AND lone-LF lines) is NOT uniform, so stage() must never reach
 # _stage_crlf_preserving and the staged blob must equal what a plain `git add` in a
 # sibling repo produces, byte for byte. (A weakened guard normalizes this worktree
 # to all-CRLF -- measured to differ from the verbatim plain-add blob.)
 wt=b'line one\r\nline TWO CHANGED\nline three\r\nline four\nline five\r\n'
 d,r=mixed_repo()
 assert not is_uniform_crlf(blob_bytes(r,'HEAD:f.md')),'fixture must have a MIXED HEAD blob'
 (r/'f.md').write_bytes(wt)
 g.stage(str(r),['f.md'])
 idx_sha=run(r,'ls-files','--stage','--','f.md').stdout.split()[1]
 d2,r2=mixed_repo(); (r2/'f.md').write_bytes(wt)
 run(r2,'--literal-pathspecs','add','--','f.md')
 ref_sha=run(r2,'ls-files','--stage','--','f.md').stdout.split()[1]
 assert idx_sha==ref_sha,'a MIXED HEAD blob must keep the plain git add blob'
 assert blob_bytes(r,idx_sha)==blob_bytes(r2,ref_sha)
 d.cleanup(); d2.cleanup()

def test_commit_refuses_eol_churn_before_head_moves():
 # Pins commit()'s TRAP-B guard (`if not allow_eol_churn: churn=_eol_churn_paths(root)
 # ; if churn: raise GitToolError('EOL_CHURN_STAGED',...)) and _eol_churn_paths's
 # per-path raw-vs-`--ignore-cr-at-eol` numstat comparison. The churned LF index blob
 # is built by hand with hash-object --no-filters, exactly the state the pre-fix
 # git_stage left behind.
 d,r=crlf_repo(); head_before=run(r,'rev-parse','HEAD').stdout.strip()
 (r/'f.md').write_bytes(b'line one\nline TWO CHANGED\nline three\nline four\nline five\n')
 sha=run(r,'hash-object','-w','--no-filters','--','f.md').stdout.strip()
 run(r,'update-index','--cacheinfo','100644',sha,'f.md')
 res=g.safe_call(g.commit,str(r),'churn','auto')
 assert res['error_name']=='EOL_CHURN_STAGED'
 churn=res['eol_churn_paths']; assert len(churn)==1 and churn[0]['path']=='f.md'
 assert (churn[0]['added'],churn[0]['deleted'])==('5','5')
 assert (churn[0]['added_ignore_cr'],churn[0]['deleted_ignore_cr'])==('1','1')
 assert run(r,'rev-parse','HEAD').stdout.strip()==head_before,'HEAD moved despite the refusal'
 d.cleanup()

def test_commit_allow_eol_churn_commits_the_renormalization():
 # Pins the allow_eol_churn parameter: commit()'s signature default (False) and the
 # guard being SKIPPED on true (`if not allow_eol_churn:` not entered), plus its
 # publication in the git_commit spec row in orchestrator_server.py.
 d,r=crlf_repo(); head_before=run(r,'rev-parse','HEAD').stdout.strip()
 (r/'f.md').write_bytes(b'line one\nline TWO CHANGED\nline three\nline four\nline five\n')
 sha=run(r,'hash-object','-w','--no-filters','--','f.md').stdout.strip()
 run(r,'update-index','--cacheinfo','100644',sha,'f.md')
 res=g.commit(str(r),'renormalize','auto',allow_eol_churn=True)
 assert res['ok'] and run(r,'rev-parse','HEAD').stdout.strip()!=head_before
 assert not is_uniform_crlf(blob_bytes(r,'HEAD:f.md')),'the intentional LF renormalization did not land'
 d.cleanup()

def test_stage_then_commit_of_a_crlf_head_blob_passes_the_gate():
 # End-to-end for the trap itself: the SAME worktree edit that the pre-fix flow turned
 # into a 5/5-vs-1/1 churn commit now stages CRLF (stage()'s TRAP-A loop) and therefore
 # sails through commit()'s TRAP-B guard with no allow_eol_churn needed.
 d,r=crlf_repo()
 (r/'f.md').write_bytes(b'line one\nline TWO CHANGED\nline three\nline four\nline five\n')
 st=g.stage(str(r),['f.md'])
 c=g.commit(str(r),'one line change',st['after']['staged_hash'])
 assert c['ok'] and is_uniform_crlf(blob_bytes(r,'HEAD:f.md'))
 d.cleanup()
