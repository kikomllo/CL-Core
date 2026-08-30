with open("src/clUI.py", "r") as f:
    text = f.read()

# Add UIScalerInjector right after imports
if "from src.clUIScalerInjector import inject_scaler" not in text:
    target = "from ui.clLogWidget import LogWidget\n"
    replacement = target + "\nfrom src.clUIScalerInjector import inject_scaler\ninject_scaler()\n"
    text = text.replace(target, replacement)

# Replace the text_input.raise() at the end of set_fullscreen
t_find = """            if getattr(self, 'text_input', None) is not None:
                self.text_input.show()
                self.text_input.raise_()"""
t_repl = """            if getattr(self, 'text_input', None) is not None:
                self.text_input.show()
                
            self._enforce_z_order()"""
text = text.replace(t_find, t_repl)

with open("src/clUI.py", "w") as f:
    f.write(text)
