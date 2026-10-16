"""Read a model's scenario choice; all prices and conditions still come from code."""
import re

_TAG = re.compile(r'<plan_setup\s+id="([a-z_]+)"\s*/>', re.I)


def extract(text, setups):
    choices = _TAG.findall(text)
    clean = _TAG.sub('', text)
    if len(choices) != 1 or '<plan_setup' in clean.lower():
        return clean, None, 'Append exactly one <plan_setup id="scenario"/> tag, using an available setup ID or wait.'
    choice = choices[0].lower()
    if choice == 'wait':
        return clean, choice, None
    setup = next((s for s in setups if s['id'] == choice), None)
    if setup is None or setup['state'] not in ('conditional', 'exit_triggered'):
        return clean, None, 'The selected setup is unavailable or blocked; choose an eligible setup or wait.'
    return clean, choice, None
