class Grade:
    @staticmethod
    def get_grade(stand):
            if stand == "stand1":
                grade = "Test-WorkStation"
            elif stand == "stand2":
                grade = "Test-WorkStation"
            elif stand == "stand3":
                grade = "LowServer"
            elif stand == "stand4":
                grade = "MiddleServer"
            else:
                grade = stand
            return grade