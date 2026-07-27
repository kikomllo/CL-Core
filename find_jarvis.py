import json

target = 'clJarvis.py'
results = []

with open('/home/kikomlo/.gemini/antigravity-ide/brain/049c0787-ba5b-455e-98a4-719b5ed2f058/.system_generated/logs/transcript_full.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        data = json.loads(line)
        step = data.get('step_index', 0)
        if data.get('type') in ['PLANNER_RESPONSE', 'CODE_ACTION']:
            for call in data.get('tool_calls', []):
                args = call.get('args', {})
                tf = args.get('TargetFile', '')
                if target in tf:
                    results.append((step, call.get('name'), args))

# Print ALL results with full content
for step, name, args in results:
    print(f"\n=== Step {step}: {name} ===")
    if name == 'multi_replace_file_content':
        for chunk in args.get('ReplacementChunks', []):
            print(f"  --- CHUNK ---")
            print(f"  Target: {repr(chunk.get('TargetContent','')[:200])}")
            print(f"  Replace: {repr(chunk.get('ReplacementContent','')[:300])}")
    elif name == 'replace_file_content':
        print(f"  Target: {repr(args.get('TargetContent','')[:200])}")
        print(f"  Replace: {repr(args.get('ReplacementContent','')[:300])}")
