$(document).ready(function() {
    $(".translate-file-form").on("submit", function(e) {
        e.preventDefault();

        $('.translate-form').attr('data-status', 'launching');

        let data = new FormData();
        data.append("user_file", document.querySelector("#user_file").files[0])
        data.append("engine_id", $(".engine-select option:selected").val())

        $.ajax({
            url: $(this).attr("action"),
            method: 'POST',
            data: data,
            contentType: false,
            cache: false,
            processData: false,
            success: function(key_url) {
                $(".file_download").attr("href", key_url);
                $('.translate-form').attr('data-status', 'ready');
            }
        });

        return false;
    })
})