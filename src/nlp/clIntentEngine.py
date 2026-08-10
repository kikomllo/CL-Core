import re
import logging
from rapidfuzz import process, fuzz
from typing import Dict, List, Tuple, Any

class IntentEngine:
    """Stateless, CPU-Optimized Fuzzy Intent Parser."""

    def __init__(self, intents_data: Dict[str, Any], word_to_number: Dict[str, str], abort_keywords: List[str], conversational_data: Dict[str, str] = None):
        self.intents_data = intents_data
        self.word_to_number = word_to_number
        self.abort_keywords = abort_keywords
        self.conversational_data = conversational_data or {}  # Store the responses here
        
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
        
        # Convert indefinite articles in unit phrases (an hour -> 1 hour, a minute -> 1 minute)
        text = re.sub(r'\b(?:an|a)\s+(hour|hours|hr|hrs|minute|minutes|min|mins|second|seconds|sec|secs)\b', r'1 \1', text, flags=re.IGNORECASE)
        text = re.sub(r'\bhalf\s+(?:an|a)\s+(hour|hours|hr|hrs)\b', r'30 minutes', text, flags=re.IGNORECASE)
                
        words = [self.word_to_number.get(w, w) for w in text.split()]
        return " ".join(words)

    def is_abort_command(self, text: str) -> bool:
        """Checks if the payload contains an abort command."""
        return any(kw in text for kw in self.abort_keywords)

    def extract_variables(self, chunk: str, intent_match: Dict[str, Any]) -> Dict[str, Any]:
        """Fuzzy-friendly slot extraction supporting single and multi-variable templates."""
        payload = {"action": intent_match["action_override"]}
        template = intent_match["original_template"]
        
        var_names = re.findall(r'\{(\w+)\}', template)
        if not var_names:
            return payload

        chunk_clean = re.sub(r'[^\w\s]', '', chunk).strip()

        if len(var_names) > 1:
            pat = re.escape(template)
            for v in var_names:
                pat = pat.replace(re.escape(f'{{{v}}}'), f'(?P<{v}>.+?)')
            pat = pat.replace(r'\ the\ ', r'\ (?:the\ )?')
            pat = f'^{pat}$'
            try:
                m = re.search(pat, chunk_clean, re.IGNORECASE)
                if m:
                    for k, val in m.groupdict().items():
                        val = val.strip()
                        if k in ["lum", "volume", "choice_index", "index"]:
                            nums = re.findall(r'\d+', val)
                            payload[k] = int(nums[0]) if nums else val
                        elif val.isdigit():
                            payload[k] = int(val)
                        else:
                            # Clean leading/trailing stop words from multi-slot string
                            w_list = val.split()
                            stop_words = {"the", "a", "an", "my", "to", "for", "please"}
                            while w_list and w_list[0].lower() in stop_words: w_list.pop(0)
                            while w_list and w_list[-1].lower() in stop_words: w_list.pop()
                            payload[k] = " ".join(w_list) if w_list else val
                    return payload
            except Exception:
                pass

        var_name = var_names[0]
        clean_template = re.sub(r'\{.*?\}', '', template).strip()
        chunk_words = chunk_clean.split()
        
        template_words = set(re.sub(r'[^\w\s]', '', clean_template).split())
        expanded_template_words = set()
        for w in template_words:
            expanded_template_words.add(w)
            if w.endswith('s'): expanded_template_words.add(w[:-1])
            else: expanded_template_words.add(w + 's')
            
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
            elif var_name == "light_target" and any(w in str(variable_value).lower().split() for w in ["all", "everything", "every"]):
                payload[var_name] = "all"
            else:
                payload[var_name] = variable_value
                
        return payload
    
    def check_conversational(self, text: str) -> str:
        """
        Checks if any conversational key exists inside the phrase.
        Returns the mapped response string if found, or None.
        """
        if not self.conversational_data:
            return None

        clean_phrase = re.sub(r'[^\w\s]', '', text.lower()).strip()

        for key, response in self.conversational_data.items():
            clean_key = re.sub(r'[^\w\s]', '', key.lower()).strip()
            if clean_key in clean_phrase:
                if isinstance(response, str):
                    return {"text": response, "action": "conversational", "target_topic": "jarvis/sys/control"}
                elif isinstance(response, dict):
                    return {
                        "text": response.get("text", ""),
                        "action": response.get("action", "conversational"),
                        "target_topic": response.get("target_topic", "jarvis/sys/control")
                    }
                return None
                
        return None

    def parse(self, text: str, raw_text: str = None) -> List[Tuple[Dict[str, Any], str]]:
        """Parses a normalized text string into actionable intents."""
        text = text.replace(",", "")
        text = text.replace("playlists", "playlist").replace("lights", "light").replace("songs", "song")

        # Protect 'and' in duration phrases like '1h and 18min', 'an hour and twenty minutes', '5 mins and 10 secs'
        num_or_word = r'(?:\d+|an|a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty|half|half\s+an)'
        unit = r'(?:h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)'
        time_and_pattern = rf'(\b{num_or_word}\s*{unit}?)\s+and\s+({num_or_word}\s*{unit}\b)'
        protected_text = re.sub(time_and_pattern, r'\1 __AND__ \2', text, flags=re.IGNORECASE)

        chunks = re.split(r'\b(?:and|then)\b', protected_text)
        executed_intents = []
        
        for chunk in chunks:
            chunk = chunk.replace('__AND__', 'and').strip()
            if len(chunk) < 3: continue
            
            chunk_tokens = chunk.split()
            
            # C++ optimized pre-filter to find top 20 candidates rapidly
            pre_candidates = process.extract(
                chunk, 
                self.flat_templates, 
                scorer=fuzz.token_set_ratio, 
                limit=20
            )
            
            scored_candidates = []
            for template_str, ts_score, _ in pre_candidates:
                p_score = fuzz.partial_ratio(chunk, template_str)
                score = (ts_score * 0.5) + (p_score * 0.5)
                
                # Length penalty: penalize short chunks matching long templates (e.g. "you" matching "you can chill")
                chunk_len = len(chunk)
                template_len = len(template_str)
                if chunk_len < template_len:
                    penalty = (chunk_len / template_len) ** 0.5
                    score = score * penalty

                if score >= 60:
                    scored_candidates.append((template_str, score))
                    
            scored_candidates.sort(key=lambda x: x[1], reverse=True)
            top_candidates = scored_candidates[:10]
            
            scored_matches = []
            for match_str, score in top_candidates:
                intents_list = self.template_to_intent[match_str]
                for intent_info in intents_list:
                    payload = self.extract_variables(chunk, intent_info)
                    
                    p_words = intent_info.get("priority_words", [])
                    boost = 1.0
                    for pw in p_words:
                        if re.search(rf'\b{re.escape(pw)}\b', chunk, re.IGNORECASE):
                            boost = 1.25
                            break
                        if len(pw) >= 4 and any(fuzz.ratio(pw, t) >= 80 for t in chunk_tokens):
                            boost = 1.25
                            break
                            
                    final_score = score * boost
                    scored_matches.append((match_str, intent_info, payload, final_score))
            
            valid_matches = [m for m in scored_matches if m[3] >= 80]
            
            if valid_matches:
                valid_matches.sort(key=lambda x: (x[3], len(x[0])), reverse=True)
                best_match = valid_matches[0]
                executed_intents.append((best_match[2], best_match[1]["target_topic"]))
            else:
                # --- DEBUG LOGGING FOR FAILED INTENTS ---
                if scored_matches:
                    scored_matches.sort(key=lambda x: x[3], reverse=True)
                    top_candidate = scored_matches[0]
                    logging.info(f"[NLP NO MATCH] Chunk: '{chunk}' | Best Candidate: '{top_candidate[1]['original_template']}' | Score: {top_candidate[3]:.1f}/100 (Threshold: 80)")
                else:
                    logging.info(f"[NLP NO MATCH] Chunk: '{chunk}' | No candidates found.")
                
        # --- MODULAR CONVERSATIONAL FALLBACK ---
        if not executed_intents:
            convo_response = self.check_conversational(raw_text if raw_text else text)
            
            if convo_response:
                logging.info(f"[NLP CONVERSATION MATCH] Passing to daemon for routing.")
                
                target_topic = convo_response.pop("target_topic", "jarvis/sys/control")
                executed_intents.append((convo_response, target_topic))
                
        return executed_intents