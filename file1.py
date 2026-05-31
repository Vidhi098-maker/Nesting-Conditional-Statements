# 1) Ask the student if they had a medical cause and store the answer in `medical_cause`.
# (Also clean the input so it becomes either 'Y' or 'N'.)
# 2) If `medical_cause` is 'Y':
# - Print that the student is allowed to attend the exam.
# 3) Otherwise (medical_cause is 'N'):
# a) Ask for the student’s attendance percentage and store it in `atten`.
# b) If `atten` is 75 or more:
# - Print "Allowed"
# c) Else:
# - Print "Not allowed"


medical_cause = input("Do they have a medical cause Y/N").upper().strip()[0:1]
if medical_cause == "Y":
    print("The student is allowed to attend the exam")
else:
    atten = int(input("Enter the student's attendance percentage"))
    if atten >= 75:
        print("Allowed")
    else:
        print("Not Allowed")