$(document).ready(function() {
    let bpl_table = $(".bleu-line").DataTable({
        "lengthMenu": [5, 10, 15, 25, 50, 100]
    });


    $('.evaluate-form').on('submit', function() {
        // Clean previous
        $(".evaluate-results").addClass("d-none");
        $(".evaluate-results-row").empty();
        bpl_table.clear().draw();

        let data = new FormData();
        data.append("mt_file", document.querySelector("#mt_file").files[0])
        data.append("ht_file", document.querySelector("#ht_file").files[0])

        $('.evaluate-status').attr('data-status', 'pending');

        $.ajax({
            url: $(this).attr("action"),
            method: 'POST',
            data: data,
            contentType: false,
            cache: false,
            processData: false,
            success: function(evaluation) {
                for (metric of evaluation.metrics) {
                    let template = document.importNode(document.querySelector("#metric-template").content, true);
                    let [min, value, max] = metric.value;
                    let proportion = max - min;
                    let norm_value = (100 * value) / proportion;

                    $(template).find(".metric-name").html(metric.name);
                    $(template).find(".metric-value").html(value);
                    $(template).find(".metric-indicator").css({ "left": `calc(${norm_value}% - 8px)` })
                    $(".evaluate-results-row").append(template);
                }


                bpl_table.rows.add(evaluation.bpl).draw();


                $("#blp-graph canvas").remove();
                $("#blp-graph").append(document.createElement("canvas"));

                let bpl_chart = new Chart(document.querySelector("#blp-graph").querySelector("canvas"), {
                    type: 'bar',
                    data: {
                        labels: Array.from(Array(evaluation.bpl.length), (x, index) => index + 1),
                        datasets: [{
                            backgroundColor: 'rgba(87, 119, 144, 1)',
                            data: evaluation.bpl.map(m => m[3]),
                            categoryPercentage: 1.0,
                            barPercentage: 1.0
                        }]
                    },
                    options: {
                        responsive: true,
                        legend: {
                            display: false
                        },
                        scales: {
                            yAxes: [{
                                display: true,
                                ticks: {
                                    suggestedMin: 0, //min
                                    suggestedMax: 100 //max 
                                }
                            }]
                        }
                    }
                });

                $('.evaluate-status').attr('data-status', 'none');
                $(".evaluate-results").removeClass("d-none");
            }
        })

        return false;
    });
})