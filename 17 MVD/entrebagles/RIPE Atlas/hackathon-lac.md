# Projects

## Country level connectivity
- IXP situation
Which IXP are in a country?  Where? 
Problem: IXP detection. We can use some databases (PeeringDB, LAC-IX, other). Or try to detect from tracerouters or LACNIC whois information. We could build an ad hoc database
- IXP country I/O: how many paths go out of the IXP / out of the country. To which countries? Get a sorted list?

## Country level DNS
- Latency to top domains: which sites are the main content in each country. Latency/path length to those sites. 
- Latency to root-servers
- Latency to local ccTLDs (from the NS assigned by the ISP). Try to save historic data
Use latencymon

## Future ideas
- Country score: asignar un puntaje a cada pais segun los indicadores de arriba
- Deteccion de IXPs: en base a datos de PeeringDB, otras DBs, whois, traceroutes (BGP?)
- Upstream concentration: detect which are the upstream provider for each country. Use BGP data? Measure how many *real* upstream provider each country has


