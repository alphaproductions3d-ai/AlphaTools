# Alpha Tools

**EN** — Alpha Tools takes a Mixamo-rigged character from Blender to a playable third-person character in Unreal Engine 5.8. It has two parts that are meant to be used together:

- **Alpha Send to Unreal** (`blender-addon/`): a Blender add-on that exports the FBX, copies the textures with standard names and writes a `manifest.json`.
- **Alpha Tools** (`unreal-plugin/`): an Unreal Engine plugin that reads that export, creates the materials, retargets the Manny animations and creates the Blueprints and a GameMode.

Both are free software (GPL-3.0-or-later). Each one includes its own HTML manual (English/Spanish): open it from the *Documentation* button in Blender, or from the *Alpha Tools > Manual* menu in Unreal.

**ES** — Alpha Tools lleva un personaje con rig de Mixamo desde Blender hasta un personaje jugable en tercera persona en Unreal Engine 5.8. Tiene dos partes que se usan juntas:

- **Alpha Send to Unreal** (`blender-addon/`): addon de Blender que exporta el FBX, copia las texturas con nombres estándar y escribe un `manifest.json`.
- **Alpha Tools** (`unreal-plugin/`): plugin de Unreal Engine que lee esa exportación, crea los materiales, retargetea las animaciones de Manny y crea los Blueprints y un GameMode.

Ambos son software libre (GPL-3.0-or-later). Cada uno incluye su propio manual HTML (inglés/español): ábrelo con el botón *Documentation* en Blender o con el menú *Alpha Tools > Manual* en Unreal.

## Requirements / Requisitos

- Blender 5.2+
- Unreal Engine 5.8 with the Third Person template content / con el contenido de la plantilla Third Person
- Mixamo skeleton / esqueleto de Mixamo

## Install / Instalación

1. **Blender:** download `alpha_send_to_unreal-<version>.zip` from the Releases page, then Edit > Preferences > Get Extensions > top-right dropdown > Install from Disk.
   **Blender:** descarga `alpha_send_to_unreal-<versión>.zip` desde Releases y usa Edit > Preferences > Get Extensions > menú desplegable arriba a la derecha > Install from Disk.
2. **Unreal:** copy `unreal-plugin/AlphaProductionsTools` (or the plugin ZIP from Releases) into `<your project>/Plugins`, enable it and restart.
   **Unreal:** copia `unreal-plugin/AlphaProductionsTools` (o el ZIP del plugin desde Releases) en `<tu proyecto>/Plugins`, actívalo y reinicia.

## Status / Estado

The Unreal plugin is a beta. Cutout and emissive materials are implemented but not yet verified with a real model; Foot IK on slopes is untested; bone physics is not included. See the manual's *Known issues*.
El plugin de Unreal es una beta. Los materiales cutout y emisivos están implementados pero aún no verificados con un modelo real; el Foot IK en pendientes no está probado; no incluye física de huesos. Consulta *Problemas conocidos* en el manual.

## Contact / Contacto

alphaproductions3d@gmail.com · [YouTube](https://www.youtube.com/@AlphaProductions-UE5-blen) · [ArtStation](https://www.artstation.com/alpha-productions) · [Instagram](https://www.instagram.com/alphaproductions_3d/) · [X](https://x.com/AlphaProd3D)

## License / Licencia

GPL-3.0-or-later. See `LICENSE`.
