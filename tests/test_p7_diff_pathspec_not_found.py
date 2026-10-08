"""P7 regression: git_diff must fail loud on pathspecs that match nothing.

Before the fix, diff(paths=['no/such.py']) silently returned ok:true with
diff:"" (git --literal-pathspecs never errors on a nonexistent path), which
reads as "no changes". Now it raises PATHSPEC_NOT_FOUND naming the spec.
"""
import tempfile, subprocess
from pathlib import Path
from orchestrator import git_tools as g

def run(root,*args): return subprocess.run(['git','-C',str(root),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,text=True)
def repo():
 d=tempfile.TemporaryDirectory(ignore_cleanup_errors=True); root=Path(d.name); run(root,'init');run(root,'config','user.name','Test');run(root,'config','user.email','test@example.com');(root/'a.txt').write_text('one\n');run(root,'add','a.txt');run(root,'commit','-m','init');return d,root

def test_p7_diff_nonexistent_pathspec_fails_loud():
 d,r=repo(); res=g.safe_call(g.diff,str(r),paths=['no/such_xyz.py'])
 assert res['error_name']=='PATHSPEC_NOT_FOUND' and 'no/such_xyz.py' in res['error']; d.cleanup()

def test_p7_diff_existing_pathspec_unchanged():
 d,r=repo(); (r/'a.txt').write_text('two\n'); res=g.diff(str(r),paths=['a.txt'])
 assert res['ok'] and 'two' in res['diff']; d.cleanup()

def test_p7_diff_worktree_deleted_tracked_file_still_diffs():
 # Guard against over-correction: a worktree-deleted (but still indexed) file
 # has a meaningful deletion diff and must NOT raise PATHSPEC_NOT_FOUND.
 d,r=repo(); (r/'a.txt').unlink(); res=g.diff(str(r),paths=['a.txt'])
 assert res['ok'] and 'deleted file mode' in res['diff']; d.cleanup()

def test_p7_diff_no_pathspecs_unchanged():
 d,r=repo(); (r/'a.txt').write_text('two\n'); res=g.diff(str(r))
 assert res['ok'] and 'two' in res['diff']; d.cleanup()
