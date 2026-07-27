import re
import logging
from rapidfuzz import process, fuzz
from typing import Dict, List, Tuple, Any

class IntentEngine:
    """Stateless, CPU-Optimized Fuzzy Intent Parser."""

    def __init__(self, intents_data: Dict[str, Any], word_to_number: Dict[str, str], abort_keywords: List[str]):
        self.intents_data = intents_data
        self.word_to_number = word_to_number
        self.abort_keywords = abort_keywords
        
        self.flat_templates: List[str] = []
        self.template_to_intent: Dict[str, List[Dict[str, Any]]] = {}
        
        self._build_fuzzy_corpus()

    def _build_fuzzy_corpus(self) -> None:
        """Builds corpus and maps priority words to each template."""
        for intent_name, config in self.intents_data.items():
            p_words = [pw.lower() for pw in config.get("priority_words", [])]
            
            for template in config.get("templates", []):
                template = template.lower()
                clean_template = template.split("{")[0].strip()
                
                if clean_template:
                    if clean_template not in self.template_to_intent:
                        self.template_to_intent[clean_template] = []
                        
                    self.template_to_intent[clean_template].append({
                        "intent_name": intent_name,
                        "target_topic": config.get("target_topic"),
                        "action_override": config.get("action_override"),
                        "original_template": template,
                        "priority_words": p_words 
                    })
                    if clean_template not in self.flat_templates:
                        self.flat_templates.append(clean_template)
                        
        logging.info(f"NLP Engine initialized with {len(self.flat_templates)} fuzzy targets.")

    def reload_intents(self, new_intents_data: Dict[str, Any]) -> None:
        """Hot-reloads the intents data and rebuilds the fuzzy corpus."""
        self.intents_data = new_intents_data
        self.flat_templates.clear()
        self.template_to_intent.clear()
        self._build_fuzzy_corpus()

    def normalize_text(self, text: str) -> str:
        """Strips punctuation and applies word-to-number mappings."""
        text = text.lower().strip()
        text = re.sub(r'[^\w\s]', '', text).strip()
        
        words = [self.word_to_number.get(w, w) for w in text.split()]
        
        return " ".join(words)

    def is_abort_command(self, text: str) -> bool:
        """Checks if the payload is purely an abort command."""
        return text in self.abort_keywords

    def extract_variables(self, chunk: str, intent_match: Dict[str, Any]) -> Dict[str, Any]:
        """Fuzzy-friendly slot extraction using Typo-Forgiving Edge-Stripping."""
        payload = {"action": intent_match["action_override"]}
        template = intent_match["original_template"]
        
        if "{" in template and "}" in template:
            var_start = template.find("{") + 1
            var_end = template.find("}")
            var_name = template[var_start:var_end]
            
            clean_template = template.replace(f"{{{var_name}}}", "").strip()
            
            chunk_clean = re.sub(r'[^\w\s]', '', chunk)
            chunk_words = chunk_clean.split()
            
            template_words = set(clean_template.split())
            expanded_template_words = set()
            for w in template_words:
                expanded_template_words.add(w)
                if w.endswith('s'): 
                    expanded_template_words.add(w[:-1])
                else: 
                    expanded_template_words.add(w + 's')
                
            # Enhanced stop_words with conversational fillers
            stop_words = {
                "please", "it", "a", "some", "my", "the", "can", "you", 
                "could", "to", "for", "track", "song", "actually", 
                "just", "kindly", "literally", "now", "hey"
            }
            words_to_remove = expanded_template_words.union(stop_words)
            
            def should_strip(word: str) -> bool:
                if word in words_to_remove: return True
                if len(word) > 3:
                    for w in words_to_remove:
                        if len(w) > 3 and fuzz.ratio(word, w) >= 80:
                            return True
                return False

            while chunk_words and should_strip(chunk_words[0]): chunk_words.pop(0)
            while chunk_words and should_strip(chunk_words[-1]): chunk_words.pop()
            
            variable_value = " ".join(chunk_words)
            
            if variable_value:
                if variable_value.isdigit():
                    payload[var_name] = int(variable_value)
                elif var_name in ["lum", "volume", "choice_index", "index"]:
                    nums = re.findall(r'\d+', variable_value)
                    if nums: payload[var_name] = int(nums[0])
                    else: payload[var_name] = variable_value
                else:
                    payload[var_name] = variable_value
                    
        return payload

    def parse(self, text: str) -> List[Tuple[Dict[str, Any], str]]:
        """Parses a normalized text string into actionable intents."""
        text = text.replace(",", "")
        text = text.replace("playlists", "playlist").replace("lights", "light").replace("songs", "song")

        chunks = re.split(r'\b(?:and|then)\b', text)
        executed_intents = []
        
        for chunk in chunks:
            chunk = chunk.strip()
            if len(chunk) < 3: continue
            
            chunk_tokens = chunk.split()
            matches = process.extract(chunk, self.flat_templates, scorer=fuzz.token_set_ratio, limit=5)
            
            scored_matches = []
            for match_str, score, _ in matches:
                intents = self.template_to_intent[match_str]
                
                p_words = []
                for i in intents: p_words.extend(i.get("priority_words", []))
                
                boost = 1.0
                for pw in p_words:
                    if re.search(rf'\b{re.escape(pw)}\b', chunk, re.IGNORECASE):
                        boost = 1.25
                        break
                    if len(pw) >= 4 and any(fuzz.ratio(pw, t) >= 80 for t in chunk_tokens):
                        boost = 1.25
                        break
                
                scored_matches.append((match_str, score * boost))
            
            valid_matches = [m for m in scored_matches if m[1] >= 85]
            
            if valid_matches:
                valid_matches.sort(key=lambda x: (x[1], len(x[0])), reverse=True)
                best_match = valid_matches[0]
                
                intent_info = self.template_to_intent[best_match[0]][0]
                payload = self.extract_variables(chunk, intent_info)
                executed_intents.append((payload, intent_info["target_topic"]))
                
        return executed_intents