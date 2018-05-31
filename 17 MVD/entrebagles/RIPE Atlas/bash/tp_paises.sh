#!/bin/bash

# <a href="/siteinfo/google.com.ar">Google.com.ar</a>
for i in AR BO BR CL CO DO EC PE PY UY VE
do
	/bin/rm $i
	SALIDA=`echo "${i,,}"`
	wget https://www.alexa.com/topsites/countries/$i
	cat $i | egrep "\/siteinfo\/" | cut -d ">" -f2 | cut -d "<" -f1 | head -10 > $SALIDA
	/bin/rm $i
done

echo "{" > country
for i in AR BO BR CL CO DO EC PE PY UY VE
do
	SALIDA=`echo "${i,,}"`
	R2=`tr '\n' ',' <$SALIDA | sed 's/,$//'`
	R22=`echo $R2 | sed 's/,/","/g'`
	R1="\"${i}\":[\"" 
	R3="],"
	echo $R1$R22$R3 >> country	
done
echo "}" >> country
