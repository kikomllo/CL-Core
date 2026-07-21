import os

def bundle_jsons(output_filename="json_bundle.txt"):
    """
    Scans the current directory for all .json files and bundles them into a single text file
    formatted with Markdown for easy LLM context pasting.
    """
    # Directories to ignore to prevent massive garbage dumps
    ignore_dirs = {
        'venv', '.venv', 'env', '.env', '.git', '__pycache__', 
        'node_modules', '.pytest_cache', '.vscode', '.idea'
    }

    base_dir = os.path.abspath(os.path.dirname(__file__))
    output_path = os.path.join(base_dir, output_filename)
    
    bundled_count = 0

    print(f"Scanning for JSON files in: {base_dir}")

    with open(output_path, 'w', encoding='utf-8') as outfile:
        for root, dirs, files in os.walk(base_dir):
            # Modify dirs in-place to skip ignored directories
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            
            for file in files:
                if file.endswith('.json'):
                    filepath = os.path.join(root, file)
                    # Get the relative path for cleaner output headers
                    rel_path = os.path.relpath(filepath, base_dir)
                    
                    try:
                        with open(filepath, 'r', encoding='utf-8') as infile:
                            content = infile.read()
                            
                        # Write the Markdown formatted block to the output file
                        outfile.write(f"## File: `{rel_path}`\n")
                        outfile.write("```json\n")
                        outfile.write(content)
                        
                        # Ensure the code block closes on a new line
                        if not content.endswith('\n'):
                            outfile.write('\n')
                        outfile.write("```\n\n")
                        
                        print(f"Bundled: {rel_path}")
                        bundled_count += 1
                        
                    except Exception as e:
                        print(f"Error reading {rel_path}: {e}")

    print("-" * 40)
    print(f"Successfully bundled {bundled_count} JSON files into '{output_filename}'.")
    print("You can now open this file, copy its contents, and paste them into the chat.")

if __name__ == "__main__":
    bundle_jsons()