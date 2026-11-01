# Local game materials (not in Git, never redistributed)

Put the files **you already own** here so the project's tools can use them
without you re-reading this document:

```
materials/apk/N.O.V.A.3_premium.apk      # primary reconstruction target
materials/apk/N.O.V.A.3_freemium.apk     # independent comparative reference
materials/obb/Nova3_premium_only_OBB.rar
materials/obb/Nova3_freemium_only_OBB.rar
```

or the already-extracted form:

```
materials/obb/premium-main.obb
materials/obb/premium-patch.obb
materials/obb/freemium-main.obb
materials/obb/freemium-patch.obb
```

Why a separate directory: the owner's checkout keeps these under `originals/`
and `Archive/`; a contributor arrives with a *pre-released source archive* and
its own files, and the tools should find them the same way. `python
tools/extract/find_materials.py` reports what it finds here (or in
`originals/`, `Archive/` or the source archive's `materials/` copy) and prints
the exact commands to use them.

Rules (from AGENTS.md, unchanged):

* These files are **data for your own testing**. They are never committed,
  never uploaded, and never part of a release archive.
* Nothing in the build may require them, and no original executable may be
  linked, bundled or executed as part of the product.
* Do not place account credentials or API keys in this tree.
