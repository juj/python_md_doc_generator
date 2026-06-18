#!/usr/bin/env python3
import argparse
import os
import re
import sys
import html as html_module
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field


@dataclass
class TypeRef:
    text: str
    refid: str | None = None


@dataclass
class Param:
    type_parts: list[TypeRef] = field(default_factory=list)
    name: str = ""


@dataclass
class EnumValue:
    name: str
    brief: str = ""
    initializer: str = ""


@dataclass
class MemberDef:
    kind: str  # "variable", "function", "enum", "typedef", "define"
    name: str
    id: str = ""
    type_parts: list[TypeRef] = field(default_factory=list)
    argsstring: str = ""
    params: list[Param] = field(default_factory=list)
    brief: str = ""
    detail: str = ""
    static: bool = False
    const: bool = False
    definition: str = ""
    initializer: str = ""
    enum_values: list[EnumValue] = field(default_factory=list)


@dataclass
class CompoundDef:
    kind: str  # "class", "struct"
    name: str
    id: str = ""
    header_file: str = ""
    variables: list[MemberDef] = field(default_factory=list)
    functions: list[MemberDef] = field(default_factory=list)


@dataclass
class HeaderFile:
    name: str
    display_name: str = ""
    id: str = ""
    compounds: list[CompoundDef] = field(default_factory=list)
    free_functions: list[MemberDef] = field(default_factory=list)
    enums: list[MemberDef] = field(default_factory=list)
    typedefs: list[MemberDef] = field(default_factory=list)
    defines: list[MemberDef] = field(default_factory=list)


def extract_text(el):
    if el is None:
        return ""
    parts = []
    def walk(node):
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)
    walk(el)
    return " ".join("".join(parts).split()).strip()


def extract_type_parts(el):
    if el is None:
        return []
    parts = []
    if el.text and el.text.strip():
        parts.append(TypeRef(text=_compact_templates(el.text.strip())))
    for child in el:
        if child.tag == "ref":
            text = child.text or ""
            refid = child.get("refid", "")
            parts.append(TypeRef(text=text.strip(), refid=refid))
        if child.tail and child.tail.strip():
            parts.append(TypeRef(text=_compact_templates(child.tail.strip())))
    return parts


def extract_brief(memberdef):
    brief = extract_text(memberdef.find("briefdescription"))
    if not brief:
        brief = extract_text(memberdef.find("detaileddescription"))
    return brief


def parse_memberdef(mdef):
    kind = mdef.get("kind", "")
    name = _compact_templates(mdef.findtext("name", "").strip())
    member = MemberDef(
        kind=kind,
        name=name,
        id=mdef.get("id", ""),
        type_parts=extract_type_parts(mdef.find("type")),
        argsstring=_compact_templates(mdef.findtext("argsstring", "").strip()),
        brief=extract_brief(mdef),
        static=mdef.get("static") == "yes",
        const=mdef.get("const") == "yes",
        definition=mdef.findtext("definition", "").strip(),
        initializer=mdef.findtext("initializer", "").strip(),
    )
    for p in mdef.findall("param"):
        param = Param(
            type_parts=extract_type_parts(p.find("type")),
            name=p.findtext("declname", "").strip(),
        )
        member.params.append(param)
    for ev in mdef.findall("enumvalue"):
        enum_val = EnumValue(
            name=ev.findtext("name", "").strip(),
            brief=extract_brief(ev),
            initializer=ev.findtext("initializer", "").strip(),
        )
        member.enum_values.append(enum_val)
    return member


def _compact_templates(s):
    s = re.sub(r'\s*<\s*', '<', s)
    s = re.sub(r'\s*>\s*', '>', s)
    s = s.replace('>', '> ')
    s = re.sub(r'>\s*$', '>', s)
    s = re.sub(r'>\s*>', '>>', s)
    s = re.sub(r'>\s*\)', '>)', s)
    s = re.sub(r'>\s*,', '>,', s)
    return s


def type_parts_to_text(parts):
    return _compact_templates(" ".join(p.text for p in parts if p.text))


def _member_ignored(name, ignore_set, compound_name=None):
    if not ignore_set:
        return False
    bare = name.split('(')[0]
    if bare in ignore_set:
        return True
    if compound_name and f"{compound_name}::{bare}" in ignore_set:
        return True
    return False


def parse_xml_dir(xml_dir, ignore_set=None):
    file_compounds = {}  # id -> parsed file compounddef
    class_compounds = {}  # id -> parsed class/struct compounddef

    xml_files = [f for f in os.listdir(xml_dir) if f.endswith(".xml")]
    for fname in xml_files:
        path = os.path.join(xml_dir, fname)
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            continue
        root = tree.getroot()
        for compounddef in root.findall(".//compounddef"):
            kind = compounddef.get("kind", "")
            cid = compounddef.get("id", "")

            if kind == "file":
                cname = compounddef.findtext("compoundname", "")
                hdr = HeaderFile(name=cname, id=cid)
                loc = compounddef.find("location")
                if loc is not None:
                    hdr.display_name = os.path.basename(loc.get("file", cname))
                else:
                    hdr.display_name = cname

                for sdef in compounddef.findall("sectiondef"):
                    for mdef in sdef.findall("memberdef"):
                        member = parse_memberdef(mdef)
                        if _member_ignored(member.name, ignore_set):
                            continue
                        if member.kind == "function":
                            hdr.free_functions.append(member)
                        elif member.kind == "enum":
                            hdr.enums.append(member)
                        elif member.kind == "typedef":
                            hdr.typedefs.append(member)
                        elif member.kind == "define":
                            hdr.defines.append(member)
                file_compounds[cid] = hdr

            elif kind in ("class", "struct"):
                cname = compounddef.findtext("compoundname", "")
                if ignore_set and cname in ignore_set:
                    continue
                includes = compounddef.find("includes")
                header = includes.text if includes is not None and includes.text else ""
                comp = CompoundDef(kind=kind, name=cname, id=cid, header_file=header)

                for sdef in compounddef.findall("sectiondef"):
                    for mdef in sdef.findall("memberdef"):
                        member = parse_memberdef(mdef)
                        if _member_ignored(member.name, ignore_set, cname):
                            continue
                        if member.kind == "variable":
                            comp.variables.append(member)
                        elif member.kind == "function":
                            comp.functions.append(member)
                class_compounds[cid] = comp

    # Map classes to their header files
    header_to_file = {}
    for fid, hdr in file_compounds.items():
        header_to_file[hdr.display_name] = hdr

    for cid, comp in class_compounds.items():
        hname = comp.header_file
        if hname in header_to_file:
            header_to_file[hname].compounds.append(comp)
        else:
            h = HeaderFile(name=hname, display_name=hname)
            h.compounds.append(comp)
            header_to_file[hname] = h

    headers = sorted(header_to_file.values(), key=lambda h: h.display_name.lower())
    # Collect all known refids for link resolution
    all_refids = {}
    for comp in class_compounds.values():
        all_refids[comp.id] = comp.name
    return headers, all_refids


def format_signature_md(member):
    ret = type_parts_to_text(member.type_parts)
    args = member.argsstring
    if "__attribute__" in args:
        args = args[:args.index("__attribute__")].strip()
    sig = ""
    if ret:
        sig += ret + " "
    sig += member.name + args
    return sig


def generate_markdown(headers, outpath):
    lines = ["# API Documentation", ""]

    for hdr in headers:
        has_content = (hdr.compounds or hdr.free_functions or hdr.enums
                       or hdr.typedefs or hdr.defines)
        if not has_content:
            continue

        lines.append(f'## `#include "{hdr.display_name}"`')
        lines.append("")

        for td in hdr.typedefs:
            type_str = type_parts_to_text(td.type_parts)
            sig = f"{type_str} {td.name}"
            if td.argsstring:
                sig += td.argsstring
            line = f"**typedef** `{sig}`"
            if td.brief:
                line += f": {td.brief}"
            lines.append(line)
            lines.append("")

        for enum in hdr.enums:
            lines.append(f"**enum {enum.name}**")
            if enum.brief:
                lines.append(f": {enum.brief}")
            lines.append("")
            if enum.enum_values:
                lines.append("Values:")
                for ev in enum.enum_values:
                    line = f"- `{ev.name}`"
                    if ev.brief:
                        line += f": {ev.brief}"
                    lines.append(line)
                lines.append("")

        for comp in hdr.compounds:
            lines.append(f"**{comp.kind} {comp.name}**")
            lines.append("")
            if comp.variables:
                lines.append("Members:")
                for v in comp.variables:
                    if not v.name:
                        continue
                    type_str = type_parts_to_text(v.type_parts)
                    line = f"- `{type_str} {v.name}`" if type_str else f"- `{v.name}`"
                    if v.brief:
                        line += f": {v.brief}"
                    lines.append(line)
                lines.append("")
            instance_methods = [f for f in comp.functions if not f.static and f.name]
            static_methods = [f for f in comp.functions if f.static and f.name]
            if instance_methods:
                lines.append("Methods:")
                for f in instance_methods:
                    sig = format_signature_md(f)
                    line = f"- `{sig}`"
                    if f.brief:
                        line += f": {f.brief}"
                    lines.append(line)
                lines.append("")
            if static_methods:
                lines.append("Static methods:")
                for f in static_methods:
                    sig = format_signature_md(f)
                    line = f"- `{sig}`"
                    if f.brief:
                        line += f": {f.brief}"
                    lines.append(line)
                lines.append("")

        if hdr.free_functions:
            lines.append("Functions:")
            for f in hdr.free_functions:
                sig = format_signature_md(f)
                line = f"- `{sig}`"
                if f.brief:
                    line += f": {f.brief}"
                lines.append(line)
            lines.append("")

        if hdr.defines:
            lines.append("Defines:")
            for d in hdr.defines:
                line = f"- `{d.name}`"
                if d.initializer:
                    line += f" = `{d.initializer}`"
                if d.brief:
                    line += f": {d.brief}"
                lines.append(line)
            lines.append("")

    with open(outpath, "w", encoding="utf-8") as fout:
        fout.write("\n".join(lines))
    print(f"Markdown written to {outpath}")


_CPP_KEYWORDS = frozenset({
    "const", "static", "void", "bool", "int", "float", "char", "double",
    "unsigned", "signed", "short", "long", "struct", "class", "enum",
    "typedef", "true", "false", "inline", "virtual", "explicit",
    "template", "typename", "namespace", "volatile", "mutable",
    "constexpr", "noexcept", "override", "final", "nullptr", "auto",
    "extern", "register", "return", "if", "else", "for", "while", "do",
    "switch", "case", "break", "continue", "default", "operator",
    "nodiscard", "force_inline", "FORCE_INLINE",
})

_CPP_BUILTIN_TYPES = frozenset({
    "uint8_t", "uint16_t", "uint32_t", "uint64_t",
    "int8_t", "int16_t", "int32_t", "int64_t",
    "size_t", "ptrdiff_t", "ssize_t", "uintptr_t", "intptr_t",
    "__f32x4", "wchar_t", "char8_t", "char16_t", "char32_t",
})

_TOKEN_RE = re.compile(
    r"([a-zA-Z_]\w*)"                    # 1: identifier
    r"|(0[xX][0-9a-fA-F]+[uUlL]*"       # 2: number literal (hex or decimal)
    r"|\d+\.?\d*[fFuUlL]*)"
    r"|(&&|\|\||::|->|<<|>>)"            # 3: multi-char operator
    r"|([*&<>=,;:(){}\[\]~!+\-/|^?])"   # 4: single-char operator/punct
    r"|(\s+)"                            # 5: whitespace
    r"|(.)"                              # 6: other
)


def _hl(cls, text):
    return f'<span class="{cls}">{html_module.escape(text)}</span>'


def _highlight_tokens(text):
    out = []
    for m in _TOKEN_RE.finditer(text):
        ident, num, mop, sop, ws, other = m.groups()
        if ident:
            if ident in _CPP_KEYWORDS:
                out.append(_hl("kw", ident))
            elif ident in _CPP_BUILTIN_TYPES:
                out.append(_hl("ty", ident))
            else:
                out.append(html_module.escape(ident))
        elif num:
            out.append(_hl("nu", num))
        elif mop:
            out.append(_hl("op", mop))
        elif sop:
            out.append(_hl("op", sop))
        elif ws:
            out.append(ws)
        elif other:
            out.append(html_module.escape(other))
    return "".join(out)


def make_anchor(name):
    return name.replace("::", "-").replace(" ", "-")


def _type_part_html(p, all_refids):
    if p.refid and p.refid in all_refids:
        target_name = all_refids[p.refid]
        anchor = make_anchor(target_name)
        return f'<a class="ty" href="#{html_module.escape(anchor)}">{html_module.escape(p.text)}</a>'
    return _highlight_tokens(p.text)


def _is_template_punct(text):
    return bool(text) and all(c in '<>*&, ' for c in text)


def type_parts_to_html(parts, all_refids):
    live = [(p, _type_part_html(p, all_refids)) for p in parts if p.text]
    if not live:
        return ""
    out = [live[0][1]]
    for i in range(1, len(live)):
        prev_raw = live[i - 1][0].text
        curr_raw = live[i][0].text
        need_space = not (
            _is_template_punct(prev_raw)
            or _is_template_punct(curr_raw)
            or prev_raw.endswith('<')
            or curr_raw.startswith('>')
        )
        if need_space:
            out.append(" ")
        out.append(live[i][1])
    return "".join(out)


def _highlight_argsstring(raw, all_refids):
    if "__attribute__" in raw:
        raw = raw[:raw.index("__attribute__")].strip()
    return _highlight_tokens(raw)


def format_signature_html(member, all_refids):
    parts = []
    ret = type_parts_to_html(member.type_parts, all_refids)
    if ret:
        parts.append(ret + " ")
    parts.append(_hl("fn", member.name))
    parts.append(_highlight_argsstring(member.argsstring, all_refids))
    return "".join(parts)


def _member_type_html(member, all_refids):
    return type_parts_to_html(member.type_parts, all_refids)


_CSS = """\
:root {
  --bg: #fff; --fg: #24292e; --bg-code: #f6f8fa; --border: #d0d7de;
  --kw: #cf222e; --ty: #0550ae; --fn: #6639ba; --nu: #0a3069;
  --op: #6e7781; --pp: #8250df; --str: #0a3069; --brief: #656d76;
  --link: #0969da; --enum-val: #24292e;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --bg-code: #161b22; --border: #30363d;
    --kw: #ff7b72; --ty: #79c0ff; --fn: #d2a8ff; --nu: #a5d6ff;
    --op: #8b949e; --pp: #d2a8ff; --str: #a5d6ff; --brief: #8b949e;
    --link: #58a6ff; --enum-val: #e6edf3;
  }
}
body { font-family: ui-monospace,SFMono-Regular,SF Mono,Menlo,Consolas,Liberation Mono,monospace;
       font-size: 13px; line-height: 1.5; max-width: 960px; margin: 0 auto; padding: 20px;
       color: var(--fg); background: var(--bg); }
h1 { font-size: 1.4em; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
h2 { font-size: 1.1em; margin-top: 2em; border-bottom: 1px solid var(--border); padding-bottom: 4px; }
h3 { font-size: 1em; margin: 1.2em 0 0.3em 0; }
ul { margin: 0.2em 0; padding-left: 1.5em; }
li { margin: 2px 0; }
code { background: var(--bg-code); padding: 1px 4px; border-radius: 3px; }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
a.ty { color: var(--ty); }
a.ty:hover { color: var(--ty); }
.sig { font-family: inherit; }
.kw { color: var(--kw); }
.ty { color: var(--ty); }
.fn { color: var(--fn); font-weight: 600; }
.nu { color: var(--nu); }
.op { color: var(--op); }
.pp { color: var(--pp); font-weight: 600; }
.str { color: var(--str); }
.brief { color: var(--brief); }
.ev { color: var(--enum-val); }
.sec { font-weight: 600; color: var(--fg); }
h2 .pp { font-weight: 600; }
h2 .str { font-weight: normal; }
h3 .kw { font-weight: normal; }
h3 .ty { font-weight: 600; }"""


def generate_html(headers, all_refids, outpath):
    h = []
    h.append("<!DOCTYPE html>")
    h.append("<html><head>")
    h.append('<meta charset="utf-8">')
    h.append("<title>API Documentation</title>")
    h.append(f"<style>\n{_CSS}\n</style>")
    h.append("</head><body>")
    h.append("<h1>API Documentation</h1>")

    for hdr in headers:
        has_content = (hdr.compounds or hdr.free_functions or hdr.enums
                       or hdr.typedefs or hdr.defines)
        if not has_content:
            continue

        anchor = make_anchor(hdr.display_name)
        dn = html_module.escape(hdr.display_name)
        h.append(f'<h2 id="{html_module.escape(anchor)}">'
                 f'<span class="pp">#include</span> '
                 f'<span class="str">"{dn}"</span></h2>')

        for td in hdr.typedefs:
            type_html = _member_type_html(td, all_refids)
            args_html = _highlight_argsstring(td.argsstring, all_refids) if td.argsstring else ""
            type_str = type_parts_to_text(td.type_parts)
            sep = "" if type_str.endswith("*") or type_str.endswith("(") else " "
            line = (f'<p>{_hl("kw", "typedef")} {type_html}{sep}'
                    f'<span class="ty"><b>{html_module.escape(td.name)}</b></span>'
                    f'{args_html}')
            if td.brief:
                line += f' <span class="brief">— {html_module.escape(td.brief)}</span>'
            line += "</p>"
            h.append(line)

        for enum in hdr.enums:
            h.append(f'<h3>{_hl("kw", "enum")} '
                     f'<span class="ty">{html_module.escape(enum.name)}</span></h3>')
            if enum.enum_values:
                h.append("<ul>")
                for ev in enum.enum_values:
                    line = f'<li><span class="ev">{html_module.escape(ev.name)}</span>'
                    if ev.brief:
                        line += f' <span class="brief">— {html_module.escape(ev.brief)}</span>'
                    line += "</li>"
                    h.append(line)
                h.append("</ul>")

        for comp in hdr.compounds:
            comp_anchor = make_anchor(comp.name)
            h.append(f'<h3 id="{html_module.escape(comp_anchor)}">'
                     f'{_hl("kw", comp.kind)} '
                     f'<span class="ty">{html_module.escape(comp.name)}</span></h3>')

            if comp.variables:
                h.append('<p class="sec">Members:</p><ul>')
                for v in comp.variables:
                    if not v.name:
                        continue
                    type_html = _member_type_html(v, all_refids)
                    line = f"<li><code>{type_html} {html_module.escape(v.name)}</code>"
                    if v.brief:
                        line += f' <span class="brief">— {html_module.escape(v.brief)}</span>'
                    line += "</li>"
                    h.append(line)
                h.append("</ul>")

            instance_methods = [f for f in comp.functions if not f.static and f.name]
            static_methods = [f for f in comp.functions if f.static and f.name]
            if instance_methods:
                h.append('<p class="sec">Methods:</p><ul>')
                for f in instance_methods:
                    sig = format_signature_html(f, all_refids)
                    line = f'<li><span class="sig">{sig}</span>'
                    if f.brief:
                        line += f' <span class="brief">— {html_module.escape(f.brief)}</span>'
                    line += "</li>"
                    h.append(line)
                h.append("</ul>")
            if static_methods:
                h.append('<p class="sec">Static methods:</p><ul>')
                for f in static_methods:
                    sig = format_signature_html(f, all_refids)
                    line = f'<li><span class="sig">{sig}</span>'
                    if f.brief:
                        line += f' <span class="brief">— {html_module.escape(f.brief)}</span>'
                    line += "</li>"
                    h.append(line)
                h.append("</ul>")

        if hdr.free_functions:
            h.append('<p class="sec">Functions:</p><ul>')
            for f in hdr.free_functions:
                sig = format_signature_html(f, all_refids)
                line = f'<li><span class="sig">{sig}</span>'
                if f.brief:
                    line += f' <span class="brief">— {html_module.escape(f.brief)}</span>'
                line += "</li>"
                h.append(line)
            h.append("</ul>")

        if hdr.defines:
            h.append('<p class="sec">Defines:</p><ul>')
            for d in hdr.defines:
                line = f'<li><span class="pp">#define</span> <code>{html_module.escape(d.name)}</code>'
                if d.initializer:
                    line += f" <code>{_highlight_tokens(d.initializer)}</code>"
                if d.brief:
                    line += f' <span class="brief">— {html_module.escape(d.brief)}</span>'
                line += "</li>"
                h.append(line)
            h.append("</ul>")

    h.append("</body></html>")

    with open(outpath, "w", encoding="utf-8") as fout:
        fout.write("\n".join(h))
    print(f"HTML written to {outpath}")


def main():
    parser = argparse.ArgumentParser(description="Generate compact API docs from Doxygen XML")
    parser.add_argument("--xml", required=True, help="Path to Doxygen XML directory")
    parser.add_argument("--md", help="Output Markdown file")
    parser.add_argument("--html", help="Output HTML file")
    parser.add_argument("--docignore", help="File listing symbols to hide from output")
    args = parser.parse_args()

    if not os.path.isdir(args.xml):
        print(f"Error: {args.xml} is not a directory", file=sys.stderr)
        sys.exit(1)

    if not args.md and not args.html:
        print("Error: specify at least one of --md or --html", file=sys.stderr)
        sys.exit(1)

    ignore_set = set()
    if args.docignore:
        with open(args.docignore, encoding="utf-8") as f:
            for line in f:
                sym = line.strip()
                if sym and not sym.startswith('#'):
                    ignore_set.add(sym)

    headers, all_refids = parse_xml_dir(args.xml, ignore_set)

    if args.md:
        generate_markdown(headers, args.md)
    if args.html:
        generate_html(headers, all_refids, args.html)


if __name__ == "__main__":
    main()
