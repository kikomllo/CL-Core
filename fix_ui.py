import re

with open("src/clUI.py", "r") as f:
    code = f.read()

# Restore global import
code = code.replace("from clUIScalerInjector import inject_scaler", "from clUIScalerInjector import inject_scaler\nfrom clUIScaler import UIScaler")

# Remove manual geometry block
manual_geom_pattern = r'            # Row 1.*?            s = UIScaler\.get\(\)\.scale.*?self\.text_input\.setGeometry[^\n]*\n'
code = re.sub(manual_geom_pattern, '            self.refresh_layout()\n', code, flags=re.DOTALL)

with open("src/clUI.py", "w") as f:
    f.write(code)

print("done")
