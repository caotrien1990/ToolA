import tempfile, subprocess
from pathlib import Path
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
