    function showModal(ment) {
				$(".modal-body").html(ment);
        document.getElementById("myModal").style.display = "block";

    }

    function closeModal(url) {
        document.getElementById("myModal").style.display = "none";
		if(url){
			location.href=url;
		}
		if($("#closeurl").val()){
						location.href=$("#closeurl").val();
		}
    }

	function gogogolink(login,link,mb_9){
		if(login){
			
				if(mb_9){
					showModal("라이브 입장이 불가합니다.");
				}else{
					location.href=link;
				}
			
		}else{
			
				showModal("로그인후 이용해주세요.");

		}

	}

	function addThousandSeparator(number) {
		return number.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
	}

/*
	    window.onclick = function(event) {
        const modal = document.getElementById("myModal");
        if (event.target == modal) {
            modal.style.display = "none";
        }
    }
	*/