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
