import os
import glob
import argparse

def create_ai_bundle(specific_files=None):
    output_file = "./outputs/jarvis_ai_review.txt"
    
    # If specific files are passed via CLI, use those. Otherwise, use the default targets.
    if specific_files:
        target_files = specific_files
    else:
        # Default behavior: root runner + everything in src folder
        target_files = ["clJarvis.py"] + glob.glob("src/*.py") + glob.glob("src/utils/*.py") + glob.glob("src/nlp/*.py") + glob.glob("config/*.json")
        
    with open(output_file, "w", encoding="utf-8") as outfile:
        outfile.write("# JARVIS SMART HOME OS - FILE BUNDLE\n\n")
        
        for filepath in target_files:
            if os.path.exists(filepath):
                outfile.write(f"## File: `{filepath}`\n")
                
                extension = filepath.split(".")[-1]
                if extension == "py":
                    outfile.write("```python\n")
                elif extension == "json":
                    outfile.write("```json\n")
                else: 
                    outfile.write("```\n")
                
                try:
                    with open(filepath, "r", encoding="utf-8") as infile:
                        outfile.write(infile.read())
                except Exception as e:
                    outfile.write(f"# Error reading file: {e}\n")
                    
                outfile.write("\n```\n\n")
                print(f"Bundled: {filepath}")
            else:
                print(f"[WARNING] File not found: {filepath}")
                
    print(f"\n[SUCCESS] Project bundled into '{output_file}'. Ready for upload.")

if __name__ == "__main__":
    # Setup argparse to handle optional command-line arguments
    parser = argparse.ArgumentParser(description="Bundle JARVIS codebase for AI review.")
    parser.add_argument(
        "files", 
        nargs="*", 
        help="Specific files to bundle. If empty, defaults to clJarvis.py and src/*.py"
    )
    
    args = parser.parse_args()
    
    # Pass the parsed files (if any) to the bundler function
    create_ai_bundle(args.files)