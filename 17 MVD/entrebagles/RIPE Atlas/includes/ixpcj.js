var initIxpStats = function (element, country) {
    var ips = {};

    var urls = {
        v4: "http://sg-pub.ripe.net/emile/ixp-country-jedi/latest/" + country.toUpperCase() + "/ixpcountry/ixpcountry.v4.json",
        v6: "http://sg-pub.ripe.net/emile/ixp-country-jedi/latest/" + country.toUpperCase() + "/ixpcountry/ixpcountry.v6.json",

        mv4: "http://sg-pub.ripe.net/emile/ixp-country-jedi/latest/" + country.toUpperCase() + "/ixplans/ixplans.v4.json",
        mv6: "http://sg-pub.ripe.net/emile/ixp-country-jedi/latest/" + country.toUpperCase() + "/ixplans/ixplans.v6.json",

        details4: "http://sg-pub.ripe.net/emile/ixp-country-jedi/latest/" + country.toUpperCase() + "/common/details/v4/0000/1111/latest.json",
        details6: "http://sg-pub.ripe.net/emile/ixp-country-jedi/latest/" + country.toUpperCase() + "/common/details/v6/0000/1111/latest.json",

        cj: "http://sg-pub.ripe.net/emile/ixp-country-jedi/latest/" + country.toUpperCase() + "/ixpcountry/index.html"
    };

    setTimeout(function () {
        writeCountryStat(ips);
    }, 10000);

    var computeStats = function (protocol, data) {

        var size = data.cells.length;
        var out = [];
        var howManyOut = data.cells.filter(function(item){
            if (!item.data.in_country){

                getStatsAbroad(item);

            }
            return item.data.in_country;
        }).length;

        var howManyIxp = data.cells.filter(function(item){
            return item.data.via_ixp;
        }).length;

        var pHowManyOut = 100 - Math.round((100/size) * howManyOut);
        var pHowManyIxp = Math.round((100/size) * howManyIxp);

        element.find("#ixp-stats-base")
            .append("<div>Of the " + size + " IPv" + protocol + " analysed path, " + pHowManyOut + "% go out of the country and " + pHowManyIxp + "% pass through an IXP</div>");
    };

    var linksStats = function(protocol, data){
        var index = {};
        var parent = $('<div class="ixp-list-stat"></div>')
            .append('<div class="title-used-ixp">Most used IXP in IPv' + protocol + ':</div>');

        var content = $("<div class=\"stats-ixp-content\"></div>");

        element
            .find("#ixp-stats-used")
            .append(parent);

        parent.append(content);

        var total = 0;

        data.links.forEach(function(item) {
            if (item.ixp) {

                if (!index[item.ixp]) {
                    index[item.ixp] = 1;
                    total++;
                } else {
                    index[item.ixp]++;
                    total++;
                }
            }

        });

        var base = 100/total;

        var sortedIndex = Object.keys(index)
            .sort(function(a, b){
                return index[b] - index[a];
            })
            .map(function(item){
                return {
                    name: item,
                    percentage: Math.round(base * index[item])
                };
            });


        sortedIndex.forEach(function(item){
            content.append("<div class=\"list-item-ixp\"><div>" + item.name + "</div><div>" + item.percentage + "%</div></div>");
        });
    };

    var getLocation = function(ip){
        //mockup, remove the following line and uncomment the block under

        ips = {"168.96.7.1":"AR","168.96.0.5":"AR","200.0.204.146":"UY","200.0.204.88":"UY","200.0.204.58":"UY","38.104.95.185":1,"154.54.9.18":"US","185.70.203.63":"IT","195.22.220.211":"IT","195.22.220.165":"IT","192.168.1.1":1,"200.3.60.165":"AR","181.88.65.232":"AR","181.88.80.170":"AR","200.117.127.1":"AR","181.88.108.253":"AR","195.22.220.213":"IT","89.221.41.161":"IT","154.54.9.17":"US","38.104.95.186":1,"200.0.204.59":"UY","200.0.204.147":"UY","168.96.0.18":"AR","168.96.7.82":"AR","200.3.60.175":"AR","181.88.171.10":"AR","181.88.108.233":"AR","181.88.109.37":"AR","195.22.220.37":"IT","154.54.80.41":"US","154.54.24.193":"US","154.54.7.157":"US","154.54.30.54":"US","213.140.53.104":"ES","94.142.98.197":1,"176.52.248.54":"ES","213.140.49.34":"ES","176.52.249.38":"ES","213.140.39.117":"ES","179.40.230.242":1,"154.54.24.233":"US","154.54.24.197":"US","154.54.24.221":"US","154.54.30.66":"US","213.140.52.32":"ES","176.52.254.86":"ES","5.53.6.188":"ES","94.142.99.2":"ES","200.51.241.218":"AR","192.168.50.254":1,"179.62.48.249":"AR","200.63.145.209":"AR","213.140.39.116":"ES","213.140.49.22":"ES","94.142.98.96":1,"64.215.100.1":"US","67.16.190.70":"US","200.59.196.222":"AR","200.59.196.221":"AR","200.51.208.96":"AR","200.51.240.181":"AR","213.140.39.118":"ES","176.52.249.18":"ES","64.208.27.37":1,"186.0.255.183":"AR","192.168.96.20":1,"170.210.173.219":"AR","172.17.12.34":1,"201.217.229.1":"AR","10.25.1.1":1,"190.216.4.209":"AR","67.17.94.249":"US","154.54.10.57":"US","190.124.210.14":"AR","181.88.80.177":"AR","190.225.252.162":1,"89.221.41.187":"IT","181.88.171.8":"AR","181.88.108.236":"AR","89.221.41.171":"IT","170.210.5.1":"AR","170.210.0.35":"AR","181.88.70.73":"AR","181.88.108.18":"AR","190.225.252.166":1,"172.30.0.1":1,"190.227.229.197":"AR","181.88.145.142":"AR","195.22.220.31":"IT","185.70.203.49":"IT","181.224.208.253":"AR","168.181.72.13":"AR","176.52.248.42":"ES","213.140.39.119":"ES","200.63.145.210":"AR","179.62.48.250":"AR","195.22.220.15":"IT","181.16.201.24":"AR","185.70.203.11":"IT","195.22.220.214":"IT","181.189.170.52":"AR"};
        // $.ajax({
        //     dataType: "json",
        //     cache: false,
        //     url: "http://localhost:8086/locate/" + ip + "/best",
        //     data: {},
        //     success: function (data) {
        //         console.log(data);
        //         ips[ip] = (data.location) ? data.location.countryCodeAlpha2 : 1;
        //         console.log(JSON.stringify(ips));
        //     },
        //     error: function(error){
        //         console.log(error);
        //     }
        // });
    };

    var writeCountryStat = function (ips) {
        var countries = {};
        Object
            .keys(ips)
            .forEach(function (key) {
                var item = ips[key];

                if (item != 1 && country != item.toLowerCase()) {
                    if (!countries[item]) {
                        countries[item] = 1;
                    } else {
                        countries[item]++;
                    }
                }
            });
        // countries-list
        var countriesSorted = [];

        Object
            .keys(countries)
            .forEach(function (item) {
                countriesSorted.push({
                    countryCode: item,
                    countryName: data.countries[item.toLowerCase()].label,
                    hits: countries[item]
                })
            });

        countriesSorted.sort(function(a, b){
            return b.hits - a.hits;
        });

        countriesSorted.forEach(function (item) {
            element.find("#countries-list").append("<span>" + item.countryName + ": " + item.hits + "</span>");

        });
    };

    var getStatsAbroad = function(item){

        $.ajax({
            dataType: "json",
            cache: false,
            url: urls.details4.replace("0000", item.row).replace("1111", item.col),
            data: {},
            success: function (data) {
                data.result
                    .forEach(function(item){
                        item.result.forEach(function(res){
                            if (res.from){
                                getLocation(res.from);
                            }
                        });
                    });

                // computeStats(4, data);
            },
            error: function(error){
                console.log(error);
            }
        });
    };

    $.ajax({
        dataType: "json",
        cache: false,
        url: urls.v4,
        data: {},
        success: function (data) {
            computeStats(4, data);
        },
        error: function(error){
            console.log(error);
        }
    });

    $.ajax({
        dataType: "json",
        cache: false,
        url: urls.v6,
        data: {},
        success: function (data) {
            computeStats(6, data);
        },
        error: function(error){
            console.log(error);
        }
    });

// --- most used IXP

    $.ajax({
        dataType: "json",
        cache: false,
        url: urls.mv4,
        data: {},
        success: function (data) {
            linksStats(4, data);
        },
        error: function(error){
            console.log(error);
        }
    });

    $.ajax({
        dataType: "json",
        cache: false,
        url: urls.mv6,
        data: {},
        success: function (data) {
            linksStats(6, data);
        },
        error: function(error){
            console.log(error);
        }
    });
};
