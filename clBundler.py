import os
import glob

def create_ai_bundle():
    output_file = "jarvis_ai_review.txt"
    
    # Target the root runner and everything inside the src folder
    target_files = ["clJarvis.py"] + glob.glob("src/*.py")
    
    with open(output_file, "w", encoding="utf-8") as outfile:
        outfile.write("# JARVIS SMART HOME OS - FULL CODEBASE\n\n")
        
        for filepath in target_files:
            if os.path.exists(filepath):
                outfile.write(f"## File: `{filepath}`\n")
                outfile.write("```python\n")
                
                try:
                    with open(filepath, "r", encoding="utf-8") as infile:
                        outfile.write(infile.read())
                except Exception as e:
                    outfile.write(f"# Error reading file: {e}\n")
                    
                outfile.write("\n```\n\n")
                print(f"Bundled: {filepath}")
                
    print(f"\n[SUCCESS] Project bundled into '{output_file}'. Ready for upload.")

if __name__ == "__main__":
    create_ai_bundle()