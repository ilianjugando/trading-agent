"""Topes de cartera: un solo lugar, para el bot y para el dashboard.

Vive en config/ y no en execution/ a proposito: api/data.py necesita estos
numeros y no puede pagar el costo de importar execution.orchestrator, que
arrastra yfinance, ib_async y okx. Antes lo intentaba dentro de un
try/except que, al fallar, caia a copias escritas a mano (12, 0.60) -- o
sea, exactamente lo que el comentario de ese import decia evitar: el
dashboard mostrando un limite distinto del que el bot aplica, en silencio.
"""

# Muchas posiciones chicas es la estrategia; infinitas posiciones chicas es
# dispersion sin tesis, y ademas hace imposible monitorear cada salida.
# Medido: sin este limite el sistema abria 5 posiciones por ciclo cada 30
# minutos.
MAX_OPEN_POSITIONS = 12

# Reserva de oportunidad. Quedarse sin efectivo significa no poder tomar la
# mejor oportunidad de la semana porque el capital ya esta en las quince
# anteriores, que eran peores.
MAX_DEPLOYED_PCT = 0.60
