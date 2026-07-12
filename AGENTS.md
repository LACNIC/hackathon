# AGENTS.md

El framework comun se expone en `ai-harness/` mediante un enlace al repositorio
hermano. Antes de abrir una sesion nueva, actualizalo sin interaccion:

```bash
./ai-harness/harness/framework/update-framework.sh
```

Luego lee `ai-harness/AGENTS.md`; ese mapa indica que contexto adicional cargar
segun la tarea. El estado y la configuracion del proyecto viven exclusivamente
en `ai-harness-local/`.
