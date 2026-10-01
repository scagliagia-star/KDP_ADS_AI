"""CSS e piccoli helper di presentazione. I colori rinforzano il testo, non lo sostituiscono."""
from ui_labels import READ_ONLY_TEXT, READ_ONLY_TITLE

CSS = """
<style>
.kb{display:inline-block;padding:2px 10px;border-radius:12px;font-size:.78rem;font-weight:600;margin-right:6px;border:1px solid transparent;white-space:nowrap}
.kb-red{background:#fde8e8;color:#9b1c1c;border-color:#f5b5b5}
.kb-orange{background:#fff1e0;color:#9a4a00;border-color:#f5cf9e}
.kb-yellow{background:#fff8d6;color:#7a5d00;border-color:#efdc85}
.kb-green{background:#e3f6e8;color:#14532d;border-color:#9fd8ae}
.kb-grey{background:#eceff3;color:#3d4653;border-color:#c9d0d9}
.kb-blue{background:#e1ecff;color:#1d3f8a;border-color:#a9c2f5}
.kb-purple{background:#efe6ff;color:#4b2a8a;border-color:#cbb5f2}
.ro-banner{background:#1f2937;color:#f9fafb;padding:10px 16px;border-radius:8px;margin-bottom:12px;font-size:.92rem}
.ro-banner b{letter-spacing:.04em}
.bar-wrap{background:#eef1f5;border-radius:4px;height:10px;width:100%}
.bar{background:#4f6fb5;height:10px;border-radius:4px}
.muted{color:#6b7280;font-size:.85rem}
</style>
"""

READ_ONLY_BANNER = (f'<div class="ro-banner"><b>{READ_ONLY_TITLE}</b> &nbsp;·&nbsp; '
                    f'{READ_ONLY_TEXT}</div>')


def bar(value, maximum):
    """Barra orizzontale HTML proporzionale (valore None/0 o massimo 0 -> barra vuota)."""
    w = 0 if not value or not maximum else max(0.0, min(100.0, 100.0 * float(value) / float(maximum)))
    return f'<div class="bar-wrap"><div class="bar" style="width:{w:.0f}%"></div></div>'
