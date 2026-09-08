# AGENTS.md

El framework comun se expone en `ai-harness/` mediante un enlace al repositorio
hermano. Antes de cualquier comando, lee exclusivamente la primera linea de
`ai-harness/AGENTS.md` y aplica `AI_HARNESS_MODE`:

- `maintenance`: no actualices, sincronices ni ejecutes el harness. Puedes usar
  sus skills y recursos de lectura directamente referenciados como guias
  opcionales y leer las reglas relevantes, no `draft`, de
  `ai-harness-local/custom-project-agent/`; no leas otro estado local ni
  escribas en `ai-harness-local/`. Continua el trabajo con esas reglas propias
  de este proyecto.
- `active`: antes de abrir una sesion nueva, actualiza el framework sin
  interaccion:

```bash
./ai-harness/harness/framework/update-framework.sh
```

El mapa completo ejecuta `./ai-harness/harness/git-preflight` antes de abrir
cualquier sesion mutante nueva: exige el checkout canonico y limpio. No abras
la tarea en un linked worktree ni limpies cambios automaticamente para superar
ese gate.

Solo en `active`, lee luego el resto de `ai-harness/AGENTS.md`; ese mapa indica
que contexto adicional cargar segun la tarea. El estado y la configuracion del
proyecto viven exclusivamente en `ai-harness-local/`.

Solo en `active`, si es tu primera interaccion con el framework, pide "quiero
hacer el onboarding del harness". Tambien puedes solicitar preguntas libres o
una evaluacion; esas interacciones usan el framework comun y no abren una tarea
de producto.
