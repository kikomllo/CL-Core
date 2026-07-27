import json
import subprocess
import os
import ast

# 1. Reset tracked files to HEAD
print("Resetting repository to HEAD...")
subprocess.run(["git", "reset", "--hard", "HEAD"], check=True)

print("Replaying edits from transcript...")

# Keep track of file contents in memory
files = {}

def get_file_content(path):
    path = os.path.abspath(path)
    if path not in files:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                files[path] = f.read()
        else:
            files[path] = ""
    return files[path]

def set_file_content(path, content):
    path = os.path.abspath(path)
    files[path] = content

with open('/home/kikomlo/.gemini/antigravity-ide/brain/049c0787-ba5b-455e-98a4-719b5ed2f058/.system_generated/logs/transcript_full.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        try:
            data = json.loads(line)
        except:
            continue
            
        step_index = data.get('step_index', 0)
        if step_index >= 2179:
            break
            
        # Tool calls are in PLANNER_RESPONSE and CODE_ACTION
        if data.get('type') in ['PLANNER_RESPONSE', 'CODE_ACTION']:
            tool_calls = data.get('tool_calls', [])
            for call in tool_calls:
                name = call.get('name')
                args_str = call.get('args', {})
                # args might be a dict or a string depending on serialization, but it's usually a dict here
                args = args_str if isinstance(args_str, dict) else json.loads(args_str)
                
                if name == 'write_to_file':
                    target_file = args.get('TargetFile')
                    if target_file:
                        # Sometimes paths are relative, make absolute relative to workspace
                        if not target_file.startswith('/'):
                            target_file = os.path.join('/home/kikomlo/Desktop/Stuff/Programming/GitHub/CL-Core', target_file)
                        set_file_content(target_file, args.get('CodeContent', ''))
                        print(f"Step {step_index}: write_to_file {target_file}")
                        
                elif name == 'replace_file_content':
                    target_file = args.get('TargetFile')
                    if target_file:
                        if not target_file.startswith('/'):
                            target_file = os.path.join('/home/kikomlo/Desktop/Stuff/Programming/GitHub/CL-Core', target_file)
                        content = get_file_content(target_file)
                        tc = args.get('TargetContent', '')
                        rc = args.get('ReplacementContent', '')
                        if tc in content:
                            content = content.replace(tc, rc)
                            set_file_content(target_file, content)
                            print(f"Step {step_index}: replace_file_content {target_file}")
                        else:
                            print(f"Step {step_index}: TargetContent not found in {target_file}")
                            
                elif name == 'multi_replace_file_content':
                    target_file = args.get('TargetFile')
                    if target_file:
                        if not target_file.startswith('/'):
                            target_file = os.path.join('/home/kikomlo/Desktop/Stuff/Programming/GitHub/CL-Core', target_file)
                        content = get_file_content(target_file)
                        
                        chunks_str = args.get('ReplacementChunks', '[]')
                        chunks = chunks_str if isinstance(chunks_str, list) else json.loads(chunks_str)
                        
                        for chunk in chunks:
                            tc = chunk.get('TargetContent', '')
                            rc = chunk.get('ReplacementContent', '')
                            if tc in content:
                                content = content.replace(tc, rc)
                                print(f"Step {step_index}: multi_replace chunk applied to {target_file}")
                            else:
                                print(f"Step {step_index}: chunk TargetContent not found in {target_file}")
                                
                        set_file_content(target_file, content)

# Write all modified files to disk
print("Writing recovered files to disk...")
skip_files = ["Dockerfile", "docker-compose.yml", "asound.conf", "src/clHealth.py"]
for path, content in files.items():
    if not path.startswith('/home/kikomlo/Desktop/Stuff/Programming/GitHub/CL-Core'):
        continue
    if any(path.endswith(skip) for skip in skip_files):
        print(f"Skipping {path} to preserve recent fixes.")
        continue
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

print("Recovery complete.")
