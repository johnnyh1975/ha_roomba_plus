# Brands PR Submission Notes — Roomba+ v2.0

## What to submit

PR to: https://github.com/home-assistant/brands

### Files to add

```
custom_integrations/roomba_plus/
├── icon.png      (256×256, already in this folder)
└── icon@2x.png   (512×512, already in this folder)
```

Note: Roomba+ is a *custom* integration (HACS), not a core integration.
It belongs in `custom_integrations/`, not `core_integrations/`.

### PR description template

```
## Checklist
- [ ] The integration name in the PR title matches the integration domain: `roomba_plus`
- [ ] The icon is an SVG or a high-resolution PNG (256×256 minimum)
- [ ] The icon@2x.png is 512×512
- [ ] Brand colors are used, not a white or transparent background

## Integration details
- Domain: `roomba_plus`
- Name: Roomba+
- Repository: https://github.com/johnnyh1975/ha_roomba_plus
- HACS: yes (custom integration)
- Quality Scale: Gold
```

## Status
This is a manual process — cannot be automated from the integration repo.
Submit the PR after tagging the v2.0 release.
