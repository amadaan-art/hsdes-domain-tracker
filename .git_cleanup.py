import subprocess as sp, os

def run(cmd):
    r = sp.run(cmd, capture_output=True, text=True)
    if r.stdout.strip(): print(r.stdout.strip())
    if r.stderr.strip(): print("STDERR:", r.stderr.strip())

# Remove stray files
for f in ['subprocess', '.git_setup.py']:
    if os.path.exists(f):
        os.remove(f)
        print(f"Deleted: {f}")

run(['git', 'add', '-A'])
run(['git', 'commit', '-m', 'Remove stray shell-artifact files (subprocess, .git_setup.py)'])
run(['git', 'log', '--oneline', '-3'])
