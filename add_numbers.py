"""Simple program to add two numbers."""


def add(a: float, b: float) -> float:
    """Return the sum of a and b."""
    return a + b


if __name__ == "__main__":
    num1 = float(input("Enter first number: "))
    num2 = float(input("Enter second number: "))
    result = add(num1, num2)
    print(f"{num1} + {num2} = {result}")
