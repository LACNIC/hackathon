var injector = function(){
    var country = (utils.getUrlParam("country")[0] || "").toLowerCase();

    var parent = $('body');
    // Do something with country
    // parent.append("<h1>Statistics about " + data.countries[country].label + "<h1>");

    initIxpStats(parent.find("#ixp-stats"), country);

    parent.children().each(function(){
        $(this).html($(this).html().replace("{{COUNTRY}}", country.toUpperCase()));
        $(this).html($(this).html().replace("{{COUNTRY_NAME}}", data.countries[country].label));

        $(this).html($(this).html().replace("{{IX_COUNT}}", cc_to_ixps[country].length));
    });

};
