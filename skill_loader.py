"""
Claude Code 全局 Skill 加载器
────────────────────────────
扫描 ~/.claude/skills/ 目录，读取 SKILL.md 及 references/。
支持列出、加载、创建、写入、删除 skill。
"""
import os
import glob
import shutil

SKILLS_ROOT       = os.path.join(os.path.expanduser("~"), ".claude", "skills")
MAX_SKILL_CHARS   = 12_000
MAX_REF_CHARS     = 3_000
MAX_REFS_PER_SKILL = 5

# ── 读取 ──────────────────────────────────────────────────────────────────────

def list_skills() -> dict[str, str]:
    """返回 {skill_name: skill_dir}，只列出有 SKILL.md 的目录。"""
    result = {}
    if not os.path.isdir(SKILLS_ROOT):
        return result
    for entry in sorted(os.listdir(SKILLS_ROOT)):
        d = os.path.join(SKILLS_ROOT, entry)
        if os.path.isdir(d) and _find_skill_md(d):
            result[entry] = d
    return result


def load_skill(name: str) -> str:
    """加载 skill 完整内容（SKILL.md + references），注入 prompt 用。"""
    skills = list_skills()
    name   = _resolve_name(name, skills)
    if name.startswith("ERROR:"):
        return name.removeprefix("ERROR:")

    skill_dir = skills[name]
    md_path   = _find_skill_md(skill_dir)
    parts     = [f"# Skill: {name}\n\n{_read_file(md_path, MAX_SKILL_CHARS)}"]

    ref_dir = os.path.join(skill_dir, "references")
    if os.path.isdir(ref_dir):
        for rf in sorted(glob.glob(os.path.join(ref_dir, "*.md")))[:MAX_REFS_PER_SKILL]:
            parts.append(f"\n---\n## {os.path.basename(rf)}\n{_read_file(rf, MAX_REF_CHARS)}")

    full = "\n".join(parts)
    return full[:MAX_SKILL_CHARS] + "\n\n[...内容已截断]" if len(full) > MAX_SKILL_CHARS else full


def list_skills_text() -> str:
    """/skills 命令回复文本。"""
    skills = list_skills()
    if not skills:
        return f"未找到任何 skill\n目录：{SKILLS_ROOT}"
    lines = [f"共 {len(skills)} 个全局 Skill（{SKILLS_ROOT}）：\n"]
    for name, d in skills.items():
        md   = _find_skill_md(d)
        desc = _extract_description(md)
        ref_count = len(glob.glob(os.path.join(d, "references", "*.md")))
        ref_info  = f"  [{ref_count} refs]" if ref_count else ""
        lines.append(f"  • {name}{ref_info}" + (f"\n    {desc}" if desc else ""))
    lines += [
        "",
        "命令：",
        "  /skill <名>            查看内容",
        "  /skill <名> <消息>     激活 skill 处理任务",
        "  /skill new <名> <描述> 新建 skill",
        "  /skill write <名> <内容> 写入/覆盖 SKILL.md",
        "  /skill del <名>        删除 skill",
    ]
    return "\n".join(lines)


# ── 创建 / 修改 / 删除 ────────────────────────────────────────────────────────

def create_skill(name: str, description: str, content: str = "") -> str:
    """
    新建 skill 目录和 SKILL.md。
    若 content 为空则生成骨架模板。
    返回操作结果描述。
    """
    if not name or "/" in name or "\\" in name:
        return "❌ skill 名称不合法"

    skill_dir = os.path.join(SKILLS_ROOT, name)
    if os.path.exists(skill_dir):
        return f"❌ skill '{name}' 已存在，用 /skill write 覆盖内容"

    os.makedirs(os.path.join(skill_dir, "references"), exist_ok=True)

    if not content:
        content = _skeleton(name, description)

    md_path = os.path.join(skill_dir, "SKILL.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)

    return f"✅ 已创建 skill：{name}\n路径：{skill_dir}\n\n用 /skill {name} 查看内容"


def write_skill(name: str, content: str) -> str:
    """覆盖已有 skill 的 SKILL.md 内容。"""
    skills = list_skills()
    name   = _resolve_name(name, skills)
    if name.startswith("ERROR:"):
        return name.removeprefix("ERROR:")

    md_path = os.path.join(skills[name], "SKILL.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"✅ 已更新 skill '{name}' 的 SKILL.md"


def delete_skill(name: str) -> str:
    """删除整个 skill 目录。"""
    skills = list_skills()
    name   = _resolve_name(name, skills)
    if name.startswith("ERROR:"):
        return name.removeprefix("ERROR:")

    skill_dir = skills[name]
    shutil.rmtree(skill_dir)
    return f"✅ 已删除 skill：{name}"


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _resolve_name(name: str, skills: dict) -> str:
    """支持前缀模糊匹配，失败返回 'ERROR:...' 字符串。"""
    if name in skills:
        return name
    matches = [k for k in skills if k.startswith(name)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return f"ERROR:Skill '{name}' 不存在\n可用：{', '.join(skills) or '（无）'}"
    return f"ERROR:'{name}' 匹配多个：{', '.join(matches)}，请指定完整名称"


def _find_skill_md(skill_dir: str) -> str | None:
    for n in ("SKILL.md", "skill.md"):
        p = os.path.join(skill_dir, n)
        if os.path.isfile(p):
            return p
    return None


def _read_file(path: str, max_chars: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(max_chars)
    except Exception as e:
        return f"[读取失败: {e}]"


def _extract_description(md_path: str | None) -> str:
    if not md_path:
        return ""
    try:
        with open(md_path, encoding="utf-8", errors="replace") as f:
            for line in f.read(1000).splitlines():
                line = line.strip()
                if line.startswith("description:"):
                    return line[len("description:"):].strip().strip('"\'')[:100]
    except Exception:
        pass
    return ""


def _skeleton(name: str, description: str) -> str:
    return f"""---
name: {name}
version: 1.0.0
description: "{description}"
metadata:
  requires:
    bins: []
---

# {name}

{description}

## Usage

描述如何使用此 skill。

## Examples

```bash
# 示例命令
```
"""
